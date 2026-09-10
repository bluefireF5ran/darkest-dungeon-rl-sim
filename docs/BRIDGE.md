# BRIDGE.md

> **`bridge/CLAUDE.md` is the summary of this file**, and Claude Code loads it
> automatically when it touches `bridge/`. This file stays the full reference and
> stays authoritative; read it before changing anything under `bridge/`.

The **player** layer: `bridge/` reads the RUNNING game and says what to do. Split out of
`CLAUDE.md` because it is ~40% of that file and only sessions touching `bridge/` need it.

Precedence is unchanged: `ASSUMPTIONS.md` is the source of truth for implemented-vs-stubbed,
`CHANGELOG.md` holds the evidence behind every rule below, `PLAYBOOK.md` decides *what to do*
with the numbers. Game files outrank all of them on anything numeric.

**Five invariants from this document also live in `CLAUDE.md`**, because they constrain
`engine/` and `env/` code written by someone who never opens this file: HP is written after
gear and trinkets; `Rollout` owns its own env; `reset(seed=)` does not reproduce a fight;
`reset(auto_resolve_to_hero=False)` is what makes seeding honest; the advisor's host env is
`ExpeditionSequenceEnv`. Keep the two copies in sync.

## The three layers

Three layers. Layer 3 (`act`) is new and dry-run by default. **`tools/` is diagnostics;
`bridge/` is the player.**

```bash
python bridge/live_state.py                      # what the running game's state is, right now
python bridge/town_state.py                      # the Hamlet: roster, quests, wallet, trinkets
python bridge/dungeon_map.py <persist.map.json> --route-to rooH   # the dungeon graph and what's left in it
python bridge/curios.py <persist.map.json> --have shovel=3,holy_water=2   # which curios to touch, with what, by whom
python bridge/route.py <persist.map.json> --raid <persist.raid.json>      # where to walk next, detours priced
python bridge/camp.py --raid <persist.raid.json> --firewood 1             # camp here, or push on
python bridge/torchlight.py --raid <persist.raid.json> --table                 # shut the torch down, or hold the light
python bridge/retreat.py --raid <persist.raid.json> --model <ckpt>        # retreat or push on, priced in gold
python bridge/advisor.py --model models/dd_combat_ppo_full_expedition.zip
python bridge/advisor.py --once --raid <snap> --roster <roster>   # replay a capture
python bridge/advisor.py --model logs/full_expedition/best_model.zip --survival 12
python bridge/embark.py --model logs/full_expedition/best_model.zip   # which quest, with whom

# The whole dungeon in one loop — what you actually run beside the game.
python bridge/run.py --model logs/full_expedition/best_model.zip --log run1.log --survival 12
python bridge/run.py --replay archive/traces/raidsnaps2    # validate the marking offline

# The same thing drawn on top of the game — overlay.py CONTAINS run.py's loop
# (its own Session, its own _watch), so run ONE of the two, never both. Running
# both gives two watchers, two tallies over the same turns, one log written twice,
# and two processes staging into the same temp files. Calibrate once per SCREEN
# resolution, with a battle on screen. The game must be borderless, and DD has no
# menu item for it — it is a launch option, `-full_as_borderless 1` (see below).
python bridge/overlay.py --calibrate     # click 4 hero ranks, 4 enemy ranks, the card spot
python bridge/overlay.py --test          # draw every marker, check it lines up
python bridge/overlay.py --model logs/full_expedition/best_model.zip --log run1.log
```

1. **read** — `live_state.py` decodes `persist.raid.json` + `persist.roster.json` into a
   `LiveState` in this repo's own ids. Complete at **turn** granularity: torch, formation
   order, round/`currentturn`, the full initiative queue, enemy ranks with corpses,
   per-hero HP/stress/affliction, equipped skills, quirks, trinkets, `resolveXp`,
   provisions, combats fought so far, and every combatant's live `buff_group`
   (buffs, debuffs, DoTs, guard, riposte). See `CHANGELOG.md` for the field table.
2. **decide** — `seed.py` builds a `CombatEngine` holding that exact position;
   `advisor.py` runs a checkpoint on it and prints the move with its runner-ups. The
   human clicks. Zero compute, and it is the only test of transfer this project has.
   **`run.py` is the one you run beside the game**: it polls once and dispatches on
   where the party is — in a battle it prints the combat move plus the retreat bill,
   between rooms it prints position, route, curios, torch and campfire — so the human
   stops having to decide which of the seven layers to consult, which was most of the
   decision.
3. **act** — input injection (`bridge/act.py`, `bridge/window.py`, `bridge/layout.py`).
   Started 2026-08-16. It was withheld on the grounds that "automating clicks fixes
   nothing if the recommendation is wrong"; the recommendation is now measured, which is
   what retired that objection — **but measured at two very different numbers, and the
   difference is a trap**. Live run 7 scored **84% MATCH over 32 recommendations** with a
   human who could see the advice on screen. The offline replay over the captured corpus
   scores **~47% over 101 combat decisions** (`tools/agreement.py`), which matches the
   ~46% this file's own signal-ranking work recorded for `raidsnaps2`. Two populations;
   quote the replay one, because it is reproducible and does not move when a human does.
   The caution has not expired either:
   **nothing is sent unless a caller passes a live backend AND sets `enabled=True`**,
   and the default backend records instead of sending.

```bash
python bridge/window.py --list                  # find the game window
python bridge/layout.py --resolve               # the board, in this window's pixels
python bridge/act.py --demo                     # plan synthetic turns, send nothing
python bridge/act.py --replay archive/traces/raidsnaps2 --model <ckpt>   # dry run
python bridge/act.py --probe                    # with the game RUNNING: which backend works

python bridge/applog.py --options               # which launch flags this boot accepted
python bridge/screen.py --once                  # which screen is up right now
python bridge/menus.py --slots                  # which save slot is free, and is it reachable
python bridge/menus.py --dry                    # name each screen and its click, send nothing
python bridge/run.py --boot --act               # front end -> a new campaign -> the tutorial
```

### What made layer 3 small: the game ships hotkeys

`localization/miscellaneous.string_table.xml` (`[pc]menu_controls_element_*`) documents
DD's PC controls: **`1`/`2`/`3`/`4`/`5` quick-select a skill**, `A`/`D` move left/right,
`T` uses a torch, `SPACE` takes ALL when the loot window is up, `TAB` toggles map and
inventory.

The `1..5` numbering *is* `overlay_slots.py`'s (`MOVE_SLOT = 5`) and *is* what
`run.py::advised_button` already computes. So **a combat turn is one keystroke plus one
click** — press the digit, click the target rank — and the eight rank points were already
calibrated. Movement and torch need no coordinates at all, which closes most of the gap
the overlay left open (it has pixels for combat only).

### Coordinates are fractions of the CLIENT rect, not screen pixels

`bridge/window.py` finds the window by **process image name** (the title is localized) and
resolves its client area with `GetClientRect` + `ClientToScreen`. **The window rect is not
the client rect** — measured on a real bordered window, window `-8,-8 .. 2568,1400` against
client `0,0 .. 2560,1392`. Using `GetWindowRect` offsets every click by a titlebar, which
reads as "it clicked the wrong hero", not as a coordinate bug.

`bridge/layout.py` stores every point as a fraction of that client area, because DD scales
its 16:9 layout to fill whatever rect it is given. **The existing 12-click calibration is
not thrown away**: it was taken borderless-fullscreen at 2560x1440, so client == screen and
dividing by the recorded `screen` recovers the fraction exactly — pinned as a round-trip
test. A non-16:9 client is *reported* (`aspect_warning`), never silently used: DD
letterboxes, so the play area is inset and a plain fraction lands off-target.

`safe()` exists because `window.py` hit `UnicodeEncodeError` on its first run, from a
terminal tab titled with U+25D1. Window titles are arbitrary OS Unicode — the same trap
`Session.emit` already carries a rule for.

### Rules that are not preferences

- **A provisional update is never actuated.** The look-ahead exists so the screen is not
  blank through ~17s of no save write; actuated, it plays a turn that is not due.
  **Measured at 27 of 145 battle updates (19%) across the capture corpus** — a sixth of
  all traffic, not a corner case. Same for `enemies_first` and for a pending enemy that
  can reposition.
- **An unmodelled screen pauses, it never guesses.** Curio, camp and retreat have no
  calibrated targets, so they are refused *by name*. Every refusal names the next screen
  worth modelling; a blind click-through would not.
- **A destructive input has to prove it worked, and prove it on the thing it aimed at.**
  Everywhere else the acknowledgement is "the save changed". For a discard that is not
  enough: the save changing does not say the *right* slot emptied. `_discard_slot`
  re-reads the pack and looks for that specific slot and item to be gone, and one that
  cannot be proven stops the sequence instead of shift-clicking the next slot on faith.
- **Refusing is a first-class outcome.** `InputPlan.refusal` carries why, and `--replay`
  reports rates per reason. A planner that silently emitted nothing would be
  indistinguishable from one with nothing to do.
- **Movement is self-correcting, because direction is not derivable.**
  `RoomAdvice.next_step` is an area id, the map graph is undirected, and `raid.in_area` is
  area-granular — nothing says whether a target is left or right. `Heading` presses `D`,
  reads the next save, and flips on a miss. The save is ground truth, so the loop corrects
  itself instead of needing a model of DD's facing that no shipped data supports.
- **The kill switch is F9**, polled via `GetAsyncKeyState` — not F10 (the overlay owns it)
  and not Esc (DD's menu key), for the same reason the overlay cannot bind a key: a
  background actuator receives no keystrokes.

### What input DD actually accepts (probed live, 2026-08-16)

Measured against the running game, not assumed. All of it needed the probe to be
fixed twice first, and both bugs are worth knowing:

| path | focused | unfocused |
|---|---|---|
| keys, `post-message` | **works** | no effect |
| keys, `send-input` (vk + scan) | **works** | n/a |
| keys, `scancode` only | **works** | n/a |
| mouse move, `send-input` | **works** — moves the REAL cursor | n/a |
| mouse move, `post-message` | **works** — moves only the GAME's cursor | untested |
| mouse **click**, `send-input` | **works** | n/a |
| mouse **click**, `post-message` | **DOES NOT WORK** (2026-08-17) | n/a |

**AN ABSOLUTE MOUSE EVENT MUST DECLARE THE VIRTUAL DESKTOP, and until
2026-08-30 none of them did.** `_normalise` maps a screen pixel over the
virtual desktop -- all monitors -- which is right, and its docstring says so.
But Windows only *reads* the 0..65535 range that way when the event carries
`MOUSEEVENTF_VIRTUALDESK`; without it the range spans the **primary** monitor
whatever the caller normalised against.

On a 3840x1080 desktop with a 1920x1080 primary that halves every x. Measured
with `GetCursorPos` after each send: asked (732,744), landed **(366,743)**;
asked (1200,500), landed **(600,499)**. Every x exactly half, every y exact --
because the virtual and primary *heights* happen to match, and that asymmetry
is what identified it.

It is **invisible on a single-monitor machine**, where the two interpretations
coincide, which is how it survived the probe written to settle exactly this
question. And it presents as the game's fault: the keyboard worked throughout,
so `menus.py --act` clicked Campaign three times and reported the title screen
unresponsive, which reads like the `post-message` row above rather than like a
bug on our side. `tests/bridge/test_act.py` pins the flag on both `click` and `move`.

### Absolute mouse input, and how it adapts to any desktop

```bash
python bridge/act.py --geometry     # the desktop, the DPI, and can a click hit a pixel
```

Run it on a new machine, or after moving a monitor. It exits non-zero if the
worst round-trip error exceeds one pixel, so it can gate arming the actuator.

Four rules, and each was a measured bug rather than a precaution:

- **Normalise over the VIRTUAL desktop and say so on the event.** The 0..65535
  range spans all monitors only when `MOUSEEVENTF_VIRTUALDESK` is set;
  normalising one way and flagging the other put every click at half its x on
  a 3840x1080 desktop with a 1920x1080 primary. Invisible on one monitor,
  where the two interpretations coincide.
- **Read the span and origin FRESH every call.** Plugging a monitor in,
  unplugging one or rearranging them changes all four numbers, and a cached
  span aims every later click at a desktop that no longer exists.
- **SUBTRACT the origin.** A monitor left of or above the primary makes
  `SM_XVIRTUALSCREEN`/`SM_YVIRTUALSCREEN` negative. Ignore that and every
  click on that monitor lands on its far edge.
- **Clamp.** `SendInput` silently wraps an out-of-range value, and a window
  dragged half off-screen is a legitimate thing to be asked to click.

**And then STOP PREDICTING, because the mapping is not exactly invertible.**
Sweeping the normalised value and reading the cursor back gives breakpoints 17
units apart (65536/3840 = 17.07) that fall where neither `floor`, `round`, nor
a half-pixel offset puts them, and no single linear inverse fits the low,
middle and top of one axis at once. Some pixels cannot be addressed at all:
**normalised 14654 lands on y=240 and 14655 on y=242**, repeatably, with the
cursor unclipped and both monitors at 96 dpi.

So `move` sends, reads `GetCursorPos`, and corrects by `error_px * 65536/span`
-- exact to first order -- **keeping the best result and stopping when the
error stops improving**. An unreachable pixel makes a naive loop oscillate
between its two neighbours and end further away than the first shot; it was
observed doing exactly that. Measured after: **28/40 exact, worst error 1 px,
40/40 within 1 px**, against 8/20 exact open-loop. `click` positions through
that verified move and sends the button events without re-aiming.

`_norm_axis` is split out so the arithmetic is testable over layouts this
machine has not got -- second monitor left, second monitor above, both,
portrait, 4K, three-across 8K, a one-pixel desktop -- because a
single-monitor developer machine cannot falsify any of the rules above.

**The DPI trap, which has not bitten yet and would be silent.** This process
is DPI-UNAWARE. With every monitor at 96 dpi that is harmless, and
`--geometry` confirms it. Add a scaled monitor and Windows reports virtualised
coordinates while input is placed in physical ones; the round-trip figure is
the thing to believe, and `--geometry` warns on exactly that combination.

**The click was the one thing the probe could not test, and it turned out not to
work.** A stray real click during a probe cannot be taken back, so 2026-08-16 validated
MOVE only and this file said so. Aiming the boot sequence at the title screen settled it:
`WM_MOUSEMOVE` landed — the Campaign button **highlighted**, which incidentally proves the
coordinate and the client-rect maths are right — and the `WM_LBUTTONDOWN`/`UP` after it did
nothing. That is the documented SDL split: the keyboard comes from window messages, the
mouse button partly from raw input, which `PostMessage` cannot supply.

So **`DEFAULT_BACKEND` is now `send-input`**, and it costs the operator their pointer.
`post-message` is kept for its move, which is the only path that leaves the cursor alone,
but nothing should choose it to click with. Note this was never a *regression*: no click
the bridge sent had ever been confirmed to land, because until the front end every layer
leant on the hotkeys and the one click per combat turn had only been dry-run.

**The keyboard fix was the scan code.** The first probes reported three flat
negatives on a key (`H`) that worked when pressed by hand. Cause: `wScan = 0`.
DD is SDL-based and SDL keys off scan codes, so a virtual-key-only event maps to
`SDL_SCANCODE_UNKNOWN` and is dropped — while Windows reports the call accepted
and `GetAsyncKeyState` confirms the injection landed. `PostMessage` had the same
defect in `lParam` (bits 16-23 are the scan code; posting 0 gives scan code 0).

**Background delivery does not work by any route.** `PostMessage` reaches the
game only while it is focused. So layer 3 holds the foreground, and F9 is the
stop. That is a real limit on the original goal, not a tuning problem.

**`post-message` was therefore the default** — same focus requirement as the others, and
the only path that leaves the operator's pointer where they left it — **until its click was
found not to register in live play.** The default is now `send-input`; see the click rows
in the table above.

**`--probe --auto` runs it with nobody there, and that took three fixes.**
`_ask` returned `False` on `EOFError`, so with no TTY the probe answered its
own three questions "no" and printed `-> Neither reached the game` three lines
under its own `reached input : yes`. An unanswered question is `None` now.

A send Windows confirms delivering, over a screen that then does not change,
is a THIRD outcome -- `INCONCLUSIVE`, not a failure. The probe key `H` is
contextual help and is inert on the front end, so scoring that as "no effect"
blames the backend for the probe's own choice of screen. Run it in a raid or
the Hamlet to settle the question it is actually asking.

`--auto` reads the answer off the screen, with a control: DD's menus animate,
so an idle baseline is sampled first at the same spacing and a verdict needs a
change well clear of it -- the same shape of argument as parking the pointer
on rank 1 and posting a move to rank 4. `_force_foreground` takes focus via
`AttachThreadInput` rather than asking a human to click, because
`SetForegroundWindow` from a background process is refused and merely flashes
the taskbar. Focus is still verified, never assumed.

Two probe bugs, recorded because both produced confident wrong answers:

- `GetAsyncKeyState`'s "reached input" check cleared the sticky bit **after**
  sending, consuming the evidence, so it could only ever report NO. Arm before,
  read after.
- The mouse round aimed both backends at the same point, and the `send-input`
  round leaves the real cursor exactly there — so the game highlighted it either
  way. It now parks the pointer on rank 1 and posts a move to rank **4**: if the
  highlight jumps while the pointer does not, only the posted message can have
  done it.

### Driving it: `run.py --act`

```bash
python bridge/run.py --model <ckpt> --act              # plays
python bridge/run.py --model <ckpt> --act --confirm    # asks before each combat turn
python bridge/run.py --model <ckpt> --backend dry      # plan only, send nothing
python bridge/run.py --model <ckpt> --act --full-pack abandon
```

**`--act` means act, and that took a live run to get right.** `confirm` was written
when combat was the only thing the actuator did, and it is *still* the only thing it
gates — the five out-of-combat layers walk the party, press `W` into curios,
SHIFT-CLICK discards and click the map without ever asking. So `--act` alone used to
mean "do all of that unsupervised, and then stop dead at the first fight to ask about
the single most reversible action in the game". Fran hit exactly that: *"it got until
the combat but didnt really play, i needed to click the attacks myself"*. Asking is
`--confirm` now, and it is opt-in. (`--auto` is still accepted, and is a no-op.)

Note the prompt is `input()` on the poll thread, so an **unanswered prompt is a
stopped bridge**, not a bridge waiting politely — the whole loop blocks, including
the phase changes it would otherwise be tracking.

`ActRunner` subscribes to the same `Update` stream the overlay draws and decides
nothing. Three gates: **`armed`** (off unless `--act`), **`confirm`** (off unless
`--confirm`, and combat-only), and **`turn_key`** — an action fires at most
once per turn, proven by the runner itself rather than trusted from the caller,
because a double-press here plays a second move.

**`turn_key` is unique within ONE combat, not within a raid**, and `acted` must be cleared
at every fight boundary. The key is `(battle_round, hero guid, turns_taken,
enemies_first)`; all four reset when a fight starts, so the opening turn of every combat is
`(1, guid, 0, 0)`. Carrying the set across the boundary silently dropped the first turn of
every fight after the first — four live runs of "went straight to the battle and stuck
there", always the second fight.

**Per-battle skill limits are the bridge's to remember.** 13 hero skills carry
`.per_battle_limit` (Take Aim, Barbaric YAWP, Blinding Gas, Transform, ...), the engine
enforces them, and **the save does not record them** — `skill_log` and `party_skill_log`
are empty in all 585 captures and in a live save taken while a Highwayman had already
spent Take Aim. A seeded engine therefore starts every counter at zero, and the advisor
re-recommends a button DD has greyed out; the click lands on nothing and the save never
moves, which in the log is indistinguishable from a dropped input. `ActRunner.on_played`
-> `Advisor.note_skill_used` -> `build_engine_from_live(skill_uses=)` closes it, on
**confirmed sends only** — a use recorded that never happened would take a legal skill
away for the rest of the fight.

**The bridge runs inference on one thread, and that is a performance fix, not a style
choice.** One `recommend()` is 1.5 ms, but PyTorch's OpenMP pool spin-waits ~200 ms per
thread afterwards: measured 1968.8 ms of CPU for 1.01 s of wall clock at 10 threads
against 0.0 ms at one. Two core-seconds of spin per piece of advice, on the machine
rendering the game. `quiet_torch()` at both model loads; never in `env/` or `engine/`,
where a training run would import it.

**The ack window has to outlast DD's write latency, and one baseline covers the whole
send.** `TURN_ACK_TIMEOUT` is 15s, not the 6s the other paths use, because the corpus says
6s is inside the distribution rather than past it: consecutive in-battle writes with no
human decision between them run median 4.8s / p90 6.1s / max 11.5s, and 175 of 191
in-battle writes land with a HERO next to act — so a hero's acknowledgement is that hero's
animation *plus every enemy turn before the next hero*. At 6s every send reported "did not
land" and was then credited to the retry. **The baseline belongs to the call, not the
attempt**: re-reading it per attempt turned the settle between attempts into a blind spot
where the first send's own write became the retry's baseline, and the retry played a second
action into a turn already spent. A save that moves between attempts now cancels the retry.
The measured latency is printed on success, so a live run replaces the corpus numbers with
the bridge's own.

**A move is marked against a RANK, hero-side.** `advised_target` resolves a footprint
through the engine's own `_resolve_action_targets`, but `__move__` is not a skill id, so
the lookup returns None and the fallback used to match the destination rank against the
ENEMY list — "MOVE to rank 1" reported as "advice pointed at Brigand Bloodletter (slot 1)",
and every followed move marked DIFFER. The advised destination is now compared with the
rank `observe` saw the actor end on, which is the only thing the diff can see. Latent in
the capture corpus (no decision there has both the advice and the play being a move) and
immediate the first time the bridge moved a hero live.

**Every drop says so.** A battle update that reaches the body of `_handle` always prints a
plan or a refusal, so a log showing an advisory block and then nothing means it was dropped
above that. Both silent returns (`paused`, and the `turn_key` dedupe) now report through
`_say_once`, which speaks the first time and then keeps quiet — they fire repeatedly by
design, since the poll loop republishes a turn until it is played. That cost four lines and
found a bug three sessions had missed. It also **refuses while the game
is not focused** and does *not* burn the turn doing so, so clicking the game
back makes the same turn playable. `handle` never raises: it runs on the daemon
poll thread, where an exception takes the bridge silent while the human keeps
playing and keeps believing it is watching.

### The party goes back in order after a fight (2026-08-19)

**The button alone is not enough, and the tutorial is where that shows.** It restores the
order the CAMPAIGN recorded at embark; the tutorial never embarked, so there is no default
and the click is inert. The intended order therefore has to be **watched**, not read: the
save carries the current formation and no field for the one the human arranged.
`ActRunner.marching_order` is captured from the first room block with no fight behind it,
and `_sort_into_marching_order` selection-sorts the party into it with the MOVE action —
select, `5`, click the rank — because out of combat that is the same control. It never
imposes an order it did not watch, never sorts a party whose membership changed, and
verifies each swap by re-reading the formation.


Combat reorders the party and nothing put it back, so a Move in one fight set the
formation for every fight after it. `layout.PARTY_ORDER` is the game's own "Default
Party order" button (`str_party_default_order`), located by alpha-matching its shipped
icon `panels/party_order_button.png` against a live frame: **0.9767** at scale 1.35
versus 0.4778 for the best match anywhere else, and the same box in a room, a corridor,
combat, and with the pack open.

`_reset_formation` clicks it once after every fight. **Not gated on detecting disorder**
— the save records the current formation and nothing records the intended one, so the
question cannot be asked; the button is idempotent, so asking and getting it wrong is
worse than not asking. **Reported, never confirmed**, because a party already in order
writes no save change and demanding one would read the common success as a failure.

### The full pack — `bridge/inventory.py` (2026-08-17)

The last routine interruption in the dungeon loop. A fight ends, SPACE takes all, and
if the pack is full DD answers with a modal that **writes nothing to the save** — so the
generic "no save change means the input was dropped" retry pressed SPACE into a dialog
already up. Seen live.

Three things the game's own files settled, none of them guessed:

| question | answer | where it came from |
|---|---|---|
| how do you discard? | **SHIFT-CLICK the slot** | `str_discard_item_instructions`: *"[SHIFT-CLICK] on this item to discard it."* |
| is there a confirm dialog? | no, on PC | the "Discard this item?" strings exist only in `PSN.string_table.xml`, for the controller's hold-TRIANGLE |
| does the save index match the screen? | **yes, row-major** | `end_of_battle_rewards_1_item.png`'s top row reads 2,2,6,12,6,6,8,3 — the save's slots 0-7 exactly |
| does one click take the whole stack? | **not known, and not assumed** | see below |

**SHIFT-CLICK is overloaded, and that is why the discard is not assumed to be atomic.**
The same gesture on the **torch meter** means something else entirely:

```
str_reduce_torch_tip   [SHIFT+CLICK] to reduce torch
str_snuff_torch_tip    [SHIFT+CTRL+CLICK] to snuff out torch
```

Nothing shipped says whether a shift-click on a pack *stack* takes one unit or all of it,
and the torch precedent makes "one unit" entirely plausible. The first version of
`_discard_slot` assumed atomic and tested "is slot 0 still holding `dog_treats x2`" — which
a per-unit discard **passes on the first click** (`x2` becomes `x1`, the label differs, the
slot is still occupied), so the runner would have reported the slot freed and pressed SPACE
into a second modal. The test is now the **occupied-slot count**, which is the only thing
that decides whether the loot fits, and the loop clicks again — bounded by the stack's own
`amount` — if the pack merely shrank. It re-checks the slot's item id every iteration,
because discarding may re-pack and a second click at the same coordinate would otherwise
land on whatever slid into it.

The grid, the modal's OK and the loot window's X are **measured, not calibrated** — edge-
detected off `captures/*.png` and verified by drawing the derived grid back over the
capture. They live in `bridge/layout.py` as constants rather than in `overlay_layout.json`,
because there is nothing for a human to click and a hand-clicked value would be worse.
Detection trick worth keeping: a cell border is bright at *every* y down the cell while an
icon is not, so a min-over-y profile isolates the frame lines and ignores content. It
returned identical lines in all three captures.

`inventory/*.darkest` gives stack limits and both gold prices. **`sell_gold_value` is not
a ranking of worth** and neither is "priced at zero both ways" — that was the first rule
written, and the first capture it ran against proposed discarding an heirloom portrait
and 625 gold, because heirlooms are a town currency and gold is gold. The rule is now a
**positive gate**: a candidate must be `type == "supply"` *and* 0/0, which across
everything the game defines selects `firewood` (excluded — it pays for a campfire) and
`dog_treats`. Nothing else is ever proposed.

**On the corpus that rule is safe and inert, and the honest number says so:** swept over
399 raid saves it proposes nothing at all. Of 25 *distinct* full packs, zero held chaff and
only 6 had a free stack merge. So the automatic path handles the case Fran actually hit
(two dog-treat stacks) and nineteen-in-twenty-five of the historical ones fall through to
`--full-pack`:

- `pause` (default) — hand over, printing the whole pack. Cannot lose anything.
- `abandon` — click the loot window's X and walk on. Legitimate (the modal itself offers
  "choose only what you can carry"), but **the bridge cannot see what it is walking away
  from**: loot is not in the save until it is picked up, so it can neither weigh nor
  report what was left. That asymmetry is why `pause` is the default.

**Merging split stacks is the strictly better move and is deliberately not done.**
`use_stack_limits true`, so three torch stacks holding 15 of a limit of 8 fit in two
slots. It is reported in `DiscardPlan.notes` and never executed, because merging is a
click-drag and the actuator only does discrete clicks. That is the next thing worth
building here.

### The torch meter: wired, and the pixel is decided by the GAME (2026-09-06)

The snuff is the last step of Fran's tutorial route, and it is worth **+454 gold a
fight** on the Old Road — the largest single term in `data/loot_loader.py`'s model.
It is in `plan_battle` now ("snuff, then swing"), and three live runs sent it at a
torch that did not move.

**The gesture was never in doubt.** The game ships both:

```
str_reduce_torch_tip   [SHIFT+CLICK] to reduce torch
str_snuff_torch_tip    [SHIFT+CTRL+CLICK] to snuff out torch
```

and Fran confirmed the widget by hand — *"if you spam shift+click on the torch icon
the torch will lower"*. Three other things were wrong, and they had been
indistinguishable from each other:

- **The modifier arrived too late.** SDL tracks SHIFT and CTRL from its own key
  events, so a click only reads as modified if the keydown was *processed* first.
  The dwell was **20 ms**; one frame at 60fps is 16.7 and DD does not always run at
  60. `act.MOD_DWELL` is 120 ms now. **No modified click in this project has ever
  been proven to land** — `_discard_slot` says as much in its own docstring — so
  this is not a torch-specific suspicion.
- **The save was read too eagerly.** A raid save is written per TURN, so a torch
  reduced between turns is not on disk 0.35 s later. A single read then records a
  *working* widget as a dead one — the cheapest possible way to refute the right
  pixel. `_torch_settled` re-reads `TORCH_CONFIRM_READS` times.
- **The pixel was eyeballed, and it never needed to be.** DD ships the raid HUD as
  block layout in a 1920x1080 design space, exactly like the town:

```
scripts/layout/screen.raid.darkest
  torch_layout: .pos_y 28 .gauge_offset 26 89 .gauge_size 400 4 .flamepos 960 100
  torch_info:   .mouseOverAreaOffset 0 0 .mouseOverAreaSize 200 130
                .stripMouseOverAreaOffset 0 70 .stripMouseOverAreaSize 860 24
```

**What it does not ship is what those offsets are ANCHORED to, and that is the whole
question.** Read `mouseOverAreaSize 200 130` as centred on the flame and the clickable
box is design x 860–1060; read it top-left anchored, the way a close glyph is, and it
is x 960–1160. The two readings do not overlap at their centres.
`townlayout.py` found the same thing one layer down (`confirm_dialog.base_pos` is
centre-anchored in x while a close glyph is not) and its rule is that **anchoring is
per widget and has to be established, never assumed.**

So `layout.TORCH_CANDIDATES` offers both readings and `_snuff_torch` **sweeps** them,
reading `torchlight` back from the save between each — the same save-acknowledgement
rule the town layer runs on. A dead point costs two clicks and the sweep moves on; the
point that answers is remembered on the runner (`_torch_point`) and named in the log,
which is how the constant gets back into this file instead of being rediscovered every
raid. A stray click is inert: `quest_info` sits at x 12–312 and `kill_count_display` at
1530, both outside the span used.

`python bridge/act.py --probe-torch` is the same sweep as a standalone experiment, plus
a `SHIFT+T` keystroke test — Fran's suggestion, and probed rather than assumed because
the string table names only the two mouse gestures. It needs the game **in a raid**, and
it costs light: a working click takes 25 off, which is the proof it worked.

**The offline half is already settled.** `TORCH_FLAME` samples `rgb=(247,160,54)` on
`captures/01_0_Starting_room.png` — that click was on the lit flame — so the pixel was
never obviously wrong and the dwell is the leading suspect. The measured points are
therefore kept at the HEAD of the candidate list: if one of them answers, the geometry
was right all along and `MOD_DWELL` was the bug. The log says which.

The old note, for the centre-anchoring evidence: the bar's lit portion shrinks
*symmetrically*, so across four captures the ends move (935-1624, 956-1603, 977-1582)
and the midpoint never does — which is what makes the centre the only stable target.

**The curio panel is HUD and fixed-position after all** — the worry that it was a world
object was wrong; the panel opens on arrival in the same region as the loot window. But
its button row's y **flows with the description length** (the loot panel's X sits at
1954,775 in both loot captures; the Rubble curio's at 1950,618 — same x, 157px higher), so
there is deliberately no constant for it. Its x is right-anchored and stable; acting on a
curio needs the panel *found on screen*, which the actuator cannot yet do.

### Moving the party (2026-08-17, all of it learned in live play)

Four separate rules, each one a bug first. Together they are why `_walk_toward` looks
nothing like "press the direction the route asked for".

- **`A`/`D` is HELD, not tapped.** It is the only non-discrete command in the game: DD walks
  the party for exactly as long as the key is down. `KEY_HOLD`'s 60 ms produced *"pulso D y
  si se movio ligeramente el grupo"* — a visible nudge and nothing more — and twelve of those
  nudges still ended `still in coBC`. `Step.hold` carries a per-step duration and
  `WALK_HOLD` is 0.9 s; every other command leaves it at 0 and taps, because holding a skill
  digit only risks an auto-repeat into a second press. Both backends implement it, and
  `post-message` additionally posts synthetic auto-repeat `WM_KEYDOWN`s (bit 30 set) for the
  duration, since a game that walks on key *repeats* rather than key *state* would otherwise
  see one press.
- **`A`/`D` never leaves a room.** They shuffle the party inside one — Fran checked by hand —
  but exiting is a door interaction. `plan_room` refuses a room-to-anywhere route step by
  name rather than hammering the key, which is what it did for twelve steps.
- **`W` is corridor→room and curio, and nothing else.** *"w solo va hacia cofres o de pasillo
  a sala, para salir de salas siempre se interactua con el mapa"*. So the walk loop presses
  `W` only after the corridor runs out and only when the target is a room; a
  corridor-to-corridor step never sends it, because `W` mid-corridor would interact with
  whatever curio is standing there — including one `curios.py` said to use an *item* on.
- **A door is walked THROUGH, not pressed at.** Entering a room is an animation and DD
  writes the save when it ends, so one look 0.6 s after the `W` decided the party was still
  in the corridor while it was walking into the room — live, the log said `still in coAB
  after 12 steps` about a party that finished in `rooB`. Watched in `DOOR_WAITS` slices of
  `DOOR_WAIT`, and the watch includes **`in_battle`**: a guarded curio opens a fight the
  moment the party steps in, and that is a save change that never touches `in_area`.
- **A dead end has to be recoverable, because nothing outside can retry.** `Heading` only
  ever learns from an area the party *arrived* in, so walking the wrong way down a corridor
  teaches it nothing — and `Session.room` dedupes on (area, torch bracket), so the room is
  never republished and there is no second chance from outside. An exhausted budget
  therefore flips the heading and walks back **once**, bounded at `2 * MAX_WALK_STEPS`
  presses total. **This is the fallback now, not the mechanism**: `Area.walked` reads the
  direction off the save (see below), and when it answers there is nothing to retry, so the
  walk-back is skipped.

### The position signal: `raid.areatile` + the dark run (2026-08-19)

**Read this before the section below it**, which describes the signal this replaces.

`raid.areatile` is the game's own count of tiles walked into the current area, three
fields below the `in_area` that `route.py` has read since the map decoded. It is exact and
monotone, and it carries **no direction** — it counts from whichever end the party entered
by. Per-tile `lit` supplies that: tiles go dark behind the party one at a time, so the dark
run's leading edge is where they stand and the end it grows from is the end they came in
by. `Area.standing_on` returns `(absolute tile, entered at tile 0)`.

Graded over every paired capture: the two agree **223 of 223**, against 172 right / **51
wrong** / 45 silent for the knowledge run below. `_walk_progress` uses both and **refuses
when they disagree** — two exact signals that differ is not a position, and refusing costs
one walk where guessing walks somewhere wrong. It falls back to `Area.walked` only when
`areatile` cannot be read at all.

Do not derive the entry end from the corridor's NAME. `Area.rooms` parses `coBC` to
`(rooB, rooC)`, but the tiles do not reliably run X→Y — `coBC` entered from `rooB` puts the
party at tile 4 of 5. That guess was wrong on 92 of 268 observations, and `Area.reversed_`
does not fix it.

### The save DOES carry a within-area position (`Area.walked`, 2026-08-18)

> **Superseded as the position signal on 2026-08-19**, by `areatile` + the dark run above.
> Knowledge marks tiles as REVEALED, not walked, so it can read identically at two
> different positions (`coCD` gives `..#####` at areatile 3 *and* 4) — a walk steered by it
> looks stalled while the party is moving. It remains the fallback and everything below
> about what it unlocked still holds; only "which field to steer by" changed.

Four separate refusals in this codebase were written around the sentence *"the save records
no within-area position to steer by"*. It was wrong. Per-**tile** `knowledge` rises to
`KNOWLEDGE_VISITED` behind the party as it walks, so the visited tiles form a run growing
from the end the corridor was entered by — **2517 corridor observations across the whole
capture corpus, and not one where that run is discontiguous**. `light` falls to 0 over the
same tiles one behind, which pins the frontier tile the party is standing on.

`dungeon_map.Walk` is the reading: `passed`, `total`, `from_front`, and derived `index`
(the save's own tile number, which a curio or a battle tile is keyed by), `remaining`, and
`heading`. `Area.walked` returns it, or `None` in the two cases it genuinely cannot answer —
a corridor not yet entered (`not any`), and one fully walked (`all`, which no longer says
which end it was entered by). A **room** also returns `None`, by construction: it is a
single tile, so there is no run to measure. That last one is why `plan_room`'s
room-exit refusal still stands.

Three things it unlocked, all of them previously listed as impossible:

- **Walking up to a specific tile.** `_walk_up_to` loops `A`/`D` until `index` reaches the
  curio's tile, instead of the single held press that Fran watched cover *"not even 1 square
  of the 4"*. It stops early the moment progress stalls — an obstacle, or the curio itself
  blocking the tile — rather than walking into it twelve more times.
- **Direction without walking the wrong way.** `Walk.heading` is `D` when the party entered
  at tile 0 and `A` when it entered at the far end, which is exactly the question `Heading`
  was learning by trial. `_forward_key` prefers the measurement and updates `Heading` from
  it; `_walk_toward` reads it once before the loop, so *"entered rooA (WRONG WAY - flipped
  to A)"* costs zero traversals now. When the direction was **measured**, an exhausted
  budget no longer triggers the walk-back: reversing would walk the party away from the exit
  it was heading for, which is a different failure from facing the wrong way.
- **A per-step acknowledgement**, where one is wanted. `_walk_up_to` uses it.
  `_walk_toward` deliberately does **not**: that loop's goal is to *leave* the corridor, and
  the step that leaves it is exactly the step that makes `walked` go `None`, so stopping on
  it would stop one step short of the boundary.

Two traps, both of which were live bugs before the tests caught them:

- **`index` goes DOWN for a party that entered at the far end.** A bare `index >= target`
  reports arrival on the first read and walks nowhere. The distance is measured along the
  party's own direction of travel: `ahead = target - index if from_front else index - target`,
  reached at `ahead <= 0` (which covers an overshoot), stalled at `ahead >= last`.
- **`None` means "read, and unreadable" and must not be confused with "not read".**
  `_forward_key(area, walk)` takes the caller's copy so a walk loop does not decode
  `persist.map.json` twice per step; the not-passed case needs the `_UNREAD` sentinel,
  because defaulting to `None` would silently turn every internal call into a guess.

**`_curio_active` still only reports, it never gates.** The tempting loop — press `W`, ask
whether the curio went inactive, press again — reads a success as a failure: most curios
are *loot* curios and DD writes nothing until the loot is actually taken. That is the same
trap that made the generic three-attempt `_send_and_confirm` press `W` twice more into an
open loot window, seen live on `coBC`'s sack.

### Leaving a room: the map panel (`bridge/mappanel.py`, 2026-08-18)

Corridors walk on `A`/`D`. **A room is only ever left by clicking the map**, and the game
says so itself — `str_help_raid_room_0` in `localization/miscellaneous.string_table.xml`:

> `[CLICK]` on a room in the map to begin moving to that room.

and `tutorial_popup_map_nav_description`: *"To move forth, `[CLICK]` on another room on the
map display. This will take you to the connecting hallway."*

**Two assumptions were wrong, and both came out of those strings.** The click target is the
destination **room**, not the corridor — `RoutePlan.next_step` from a room *is* the
connecting hallway, which the map does not accept as a destination, so `RoomAdvice` gained
`next_room` (the first `roo*` on `plan.walk[1:]`). And a corridor tile is never clickable at
all: the small cells on the panel are landmarks for measuring, never targets.

**The geometry was in the save the whole time, one decode further in.** `Tile.mappos` is a
contiguous grid, one unit per tile — the Old Road is `rooA` at `(-10.5, -7)`, `coAB` t0..t5
at `(-9.5..-4.5, -7)`, `rooB` at `(-3.5, -7)`. In a live save it lives in
`map.static_dynamic.static_save`, a **length-prefixed nested DSON buffer** (u32 count, then
the `01 B1 00 00` magic) exactly like a hero's `raw_data`; `static_dynamic.areas` beside it
holds only what changes. `read_dungeon_map` read only the second, which is why the bridge
spent months believing the coordinates were not in a live save. Two encodings, told apart by
container and not by a magnitude heuristic: DSON writes `mappos` and `bounds` as int32s that
are **float bit patterns** (`-1050673152` is `-14.0`), the text template writes real floats.
`map.bounds` decodes to `(min_x, max_x, min_y, max_y)` and equals the tile extent exactly in
all three captured dungeons.

**The transform is measured per frame, never stored.** Every other coordinate in `bridge/` is
a fixed fraction of the client rect because the HUD does not move; the map does. The game
ships `[MOUSEWHEEL]` zoom, `[RIGHT-CLICK]`+`[DRAG]` pan, a "Center map on party" button and
an `Auto-Center Dungeon Map` option, and three real captures measured **48.0, 15.3 and 10.7
px per map unit** — three zoom levels, not a calibration error. So `mappanel` recovers
`(scale, origin)` from the cells that are drawn against the tiles the save says exist:

- `find_boxes` segments the panel in **two passes** — column runs, row runs within each, then
  column runs again within *those*. The second pass is load-bearing: one pass gives a
  vertical corridor the full width of whatever wide room shares its column, moving its centre
  tens of pixels sideways of where it is drawn, and the centre **is** the click.
- `fit` scores each candidate scale by a translation vote. A median would be wrong and it is
  worth knowing why: the tutorial's six cells give gaps 96, 48, 48, 48, 95.5, 95.5, whose
  median is 71.75 — a scale that matches nothing. Rooms swallow the door tile either side of
  them, so plenty of gaps are 2 or 3 units.
- Two constraints keep the vote honest. **No two cells can sit closer than one unit**, so a
  candidate scale above the closest observed pair is geometrically impossible — that is what
  rejects the spurious large-scale fits, and it is an impossibility rather than a tuned
  threshold. And a cell cannot be much smaller than the scale, so `MIN_CELL_UNITS` filters
  per candidate, which resolves the circularity ("too small for what?") instead of ducking it.

**Verified against ground truth**: `captures/02_starting_room_After_map_click.png` is the Old
Road's first room with the panel open, and `profile_1` is the live save it came from. The fit
is **48.0 px/unit, 6/6 cells explained, worst residual 12.3 px** (a quarter of one unit — an
unvisited room's box is drawn asymmetrically), and it predicts every tile centre to within a
pixel without ever being told which cell was which. `rooB` resolves to fraction
`(0.7361, 0.8597)`, inside the box the game drew for it.

**Three separate refusals, because a wrong click here is not a no-op** — the panel sits over
the world, and a miss moves the party or pokes a curio. `locate` returns `None` when the area
has no coordinates, when no transform explains what is drawn, and — the one easiest to get
wrong — when the fit is perfect but **the destination is not on the map yet**: an unscouted
room simply is not drawn, and the transform will happily predict a point for it. The last
check is therefore that the predicted point lands inside a cell that was actually detected,
and only among the cells the fit itself accepted, so a 3-pixel glyph cannot stand in for a
room.

On a refusal — and on a click that the save does not confirm — `act.py` **banks the frame**
to `captures/_map_panel.png` (`screen.save_png`, `act.MAP_SHOT`, gitignored). The pixels that
would explain the failure are on screen at the exact moment the bridge gives up; asking a
human to alt-tab and reproduce that moment is a worse way to get the same image. One fixed
filename, overwritten, best-effort: Pillow is a dev-only import, and a failed write costs the
PNG and never the message saying why.

```bash
python bridge/mappanel.py <frame.png> <persist.map.json> [--area rooB]
```

**Confirmed live, and it immediately exposed two more.** The party left `rooA` through the
map on the first armed run. Then `W` sent them back: it acts on whatever the party is
**standing in front of** (`tutorial_popup_using_doors_description`), and a party entering a
corridor stands at the door it came through — so `_interact_curio` now walks `A`/`D` before
pressing `W`, and never in a room, where the party is already on the tile. It does not
retry on "the save still shows the curio": a loot curio writes nothing until the loot is
taken, so that reads success as failure.

That walk was one held press at first, and one press is not enough — *"it did walk a little
but not even 1 square of the 4"*. Making it a loop needed a way to tell whether walking had
achieved anything, which is what sent the search that found `Area.walked`.

**And then the tile turned out not to be the variable at all.** `W` failed from the
curio's own tile, from one tile short, and from two — while the same `W` opened the door at
the end of the same corridor every time. At a door the party has auto-stopped; mid-hallway
it is still gliding from a 0.9 s held key, and `W` acts on what is in front of them *now*.
So the press is **attempted and verified** rather than aimed: `SETTLE` first, press,
`_curio_landed`, and up to `CURIO_TRIES` attempts stepping one press further between them.

**Retrying is safe only because SPACE goes out BEFORE the decision.** The no-retry rule
came from `coBC`'s sack — a loot curio writes nothing until the loot is taken, so "still
active" is not evidence of failure, and the generic retry pressed `W` into an open loot
window twice more. With SPACE first, any window the press opened is already cleared, and a
**grown pack** is the positive signal a loot curio actually gives. The invariant is
unchanged: a press that landed is never repeated. Only the test for "landed" got better.

**And it was stopping one tile too far.** `W` acts on what the party is standing IN FRONT
OF, and the game stops them at a curio by itself, so walking until the save says they are
ON its tile walks them past it. The two help strings differ exactly here —
`tutorial_popup_using_doors_description` says "when standing in front of it" and
`tutorial_popup_curio_description` says only "to investigate them" — and that was read as
the curio being the *looser* case. It is the stricter one for approach. `ahead <= 1`.

Proved rather than guessed: `travellers_tent_tutorial` is a LOOT curio (2 draws of
"Someone left valuables inside the tent"), so a press that worked opens a window and the
`SPACE` after it changes the save. Twice it did not, and the tent stayed `content 7,
active True`.

**And the loop's stall rule then fired on the first press.** `Area.walked` is granular to
one TILE and `WALK_HOLD` covers well under one, so a press that does not change the tile is
the normal mid-tile case, not an obstacle — calling it a stall skipped the tent from two
tiles out. `WALK_STALL_LIMIT = 3` consecutive non-advancing reads, reset by any progress.

And the bridge was corrupting its own evidence — the cursor stayed where the click left it,
DD drew a hover border and a **"Move to this room"** tooltip, and the next frame had 19 cells
instead of 6. `_park_cursor` moves the pointer to `MENU_DEAD_ZONE` (never clicks) before every
map read.

**Not yet validated on a full-size dungeon.** The unpaired real captures fit at 15.5 px/unit
against `raidsnaps2` — matching the 15.3 pitch measured independently off the same image, so
probably right — but only 15 of 23 cells are explained, under `MIN_EXPLAINED`, and there is
no way to confirm the capture and the save are the same raid. So the threshold stands where
the one verified frame put it and real dungeons will refuse until a paired capture exists.
The banking is what produces that pairing on the next run.

### Booting from the menus, and the tutorial as a test rig (2026-08-17)

Every corridor fix so far came out of Fran narrating a stall mid-raid and it being
guessed at afterwards. **The Old Road is a rig that ends that**, because its layout is
not generated — `GAME_DIR/scripts/starting_save/persist.map.json` is hand-authored and
copied verbatim into a new slot:

```
rooA  ->  coAB t0 t1 t2 t3 t4 t5  ->  rooB
                ^battle    ^curio        ^final room (the goal)
```

Two rooms, one six-tile corridor, torch 100. Only the two fights are rolled (both battle
tiles carry a `mash_index`); the rooms, the corridor length, the curio and their order
repeat exactly. `tests/bridge/test_tutorial_map.py` pins all of it, so a corridor regression is
a failing assertion instead of an anecdote.

**Creating the save slot IS the embark.** The template ships as
`{"inraid": true, "raiddungeon": "tutorial"}`, so a new campaign starts inside the raid:
there is no town step and no Old Road to pick from a quest board.
`captures/00_8_old_road.png` is the *loading screen*, and `app.log` logs its crest as
`Continue pressed in Loading Screen` right before `as_raid_start_done_loading`.

#### Three things the front end broke, and what fixed each

**1. Nothing there writes a save, so nothing could be acknowledged.**
`bridge/applog.py` reads `GAME_DIR/app.log` — plain text, timestamped, truncated per
launch. It gives which launch flags the binary accepted (`option: <name>`; **a flag the
binary does not know is simply never echoed**, which is how a flag probe gets graded from
disk), when a campaign loaded, and when a raid started. It does **not** name the current
screen: `as_fe_flow_*` never reaches the log, only `as_load_campaign` and
`as_raid_start_done_loading` do. Checkpoints, not per-step acks — and since
`as_load_campaign` fires three times in one real session, `wait_for` counts *new* lines
rather than asking whether one ever appeared.

**2. Which screen is up was unknowable.** `bridge/screen.py` grabs the client rect with
pure ctypes GDI (`GetDC` → `BitBlt(SRCCOPY)` → `GetDIBits` with a **negative** `biHeight`,
which is what asks for a top-down buffer; a positive one mirrors every signature and the
bug survives its own test). 2560x1440 in 73 ms, `numpy` only — no new dependency, and
Pillow is imported in exactly one dev-only place, `--calibrate`.

A screen is named by the cosine distance between mean-normalised grayscale hashes of the
centre block `(870,400)-(1660,900)` at 24x14. Measured over `captures/00_*.png`, every
distinct screen sits **≥ 0.484** from its nearest neighbour. Mean-normalising is what
survives the menus' animated fire: it compares the shape of the light, not its level.

**`recognise` demands two things and the second is the load-bearing one**: the best match
must be under `THRESHOLD` *and* beat the runner-up by `MARGIN`. The DLC list and the
"Start Campaign" modal drawn over it are the closest pair, and reading the list as the
modal would click `MODAL_OK` straight into the DLC rows and switch a DLC off with nothing
to notice. A near-tie must resolve to "I do not know", and every caller treats that as a
pause. `00_3` and `00_7` share one name deliberately — they are one screen, differing only
in what row 2 holds.

**3. The save row moves.** `bridge/menus.py` is a state machine keyed on the recognised
screen, never a timed sequence, and it derives the row **from disk**: DD renders rows in
slot order and puts the empty letter at the first FREE `profile_N` index. On this machine
`profile_0` is used and `profile_1` is free, and both captures show the letter (then the
new campaign) in row 2 — exactly that rule. The rendered list scrolls; the directory does
not. `save_select` appears twice in the flow and needs no state flag, because the click is
the same both times.

**Only two rows are visible** (viewport 955..1319 of 1440, black below), so the first free
slot must be 0 or 1; at 2 it refuses and says so. That is a real limit on iterating — each
tutorial run consumes a slot — and **nothing here ever clicks a delete X**: it sits 185 px
from `DD_Model_1.2` (Week 22) and a wrong row destroys 22 weeks with no undo.
`tests/bridge/test_menus.py` proves that by exhaustion over every screen and every slot, not by
inspection.

#### Two live bugs this flow surfaced

- **`find_profile` was returning the Butcher's Circus profile.** A `profile_*` directory
  is not necessarily a campaign: the Circus keeps its own, with no `persist.game.json` at
  all, and on this machine it was the most recently touched of the three. Everything
  downstream then watched a directory that can never hold a raid, silently.
  `persist.game.json` is now the filter.
- **A freshly created slot is plain text JSON, not DSON.** Slot creation copies
  `scripts/starting_save/`'s seven files byte-for-byte and those ship as ordinary JSON,
  so `tools/dson.py` raised `bad magic` on the very files the bridge had been told to
  watch. `parse_file` now falls back on the magic alone, and `is_complete` accepts a text
  save that parses — otherwise a whole file reads as torn and every poll retries six times.

#### `-skipfeflow -profile N` does not replace the clicking — probed 2026-08-17

The hope was that two launch flags would delete this whole module. They do not, and the
probe is worth recording so nobody spends another crash finding out.

```
Darkest.exe -skipvalidation -skipfeflow -profile 2 -randomseed 12345 -resetcampaigntutorials
```

**All four flags were accepted** — `app.log` echoed `option: profile "2"`,
`randomseed "12345"`, `resetcampaigntutorials` and `skipfeflow`. So the flags are real and
**the grading method works**: `bridge/applog.py --options` tells you what the binary took,
without a screenshot. Note that `skipvalidation` lives in the `Darkest::ParseArguments`
table (0xab7bc0) while `full_as_borderless` lives in the separate **plinth** table
(0xaac2f8) — and both are echoed, so the `option:` block covers the two.

**And the game then crashed during boot**, 43 log lines in, right after
`Music - 0.000000` and before any `as_*` state at all: it never reached the front end.
`profile_2` did not exist. So **`-profile N` selects an existing slot, it does not create
one**, and with `-skipfeflow` there is no front end left to fall back to.

That kills the idea for this job rather than merely deferring it: **every tutorial run
needs a NEW campaign**, and the flag pair can only open one that is already there.

What the flags are still good for, unprobed: `-profile N` on an *existing* slot, to skip
the list when re-entering a campaign that has already been made.

**The real iteration bottleneck is not the menus — it is the slot.** Six clicks are cheap;
what is not is that each tutorial consumes a save slot and only two rows are visible, so
the second run needs the first campaign gone. Clicking the delete X is refused for good
reason, but deleting `profile_N/` **by path** is a different proposition: it names the
exact directory instead of trusting a y coordinate, so it cannot hit `DD_Model_1.2` by
being one row off. Not implemented — it is still destructive, and Fran's call.

**SUPERSEDED 2026-09-03 — launch windowed instead: `-fullscreen 0 -size
1280 720`.** Borderless is right that exclusive fullscreen is unreadable, and
wrong that borderless fixes it: a borderless window covering a whole monitor
takes DWM's **independent-flip** path and is scanned out without ever reaching
the composited surface GDI reads, so `screen.grab` comes back `min=0 max=0`
while the desktop outside the window animates normally. Measured both ways on
one boot. The paragraph below is kept because its ACCOUNT of GDI is right and
because the overlay's own constraint is unchanged.

**`full_as_borderless` must stay on.** `screen.py` captures through GDI `BitBlt` on the
window DC, and exclusive fullscreen typically hands GDI a black frame; borderless is an
ordinary window. `SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS` sits beside it in the same table, and
a game that minimises on focus loss breaks every input path, all of which need focus.

### Dry-run status (2026-08-16)

`--replay` over the capture corpus, game closed, nothing sent:

| set | battle updates | plans | actionable | refused |
|---|---|---|---|---|
| raidsnaps2 | 74 | 74 | 62 (83.8%) | 12 |
| raidsnaps3 | 19 | 19 | 15 (78.9%) | 4 |
| raidsnaps4 | 52 | 52 | 41 (78.8%) | 11 |
| **total** | **145** | **145** | **118 (81.4%)** | **27** |

Zero exceptions, zero degenerate `(0,0)` clicks, zero empty plans, and every one of the 27
refusals is the provisional look-ahead. Exactly one key and one click per actionable turn.
(`raidsnaps` yields 0 battle updates — those original 72 captures are not self-contained,
which is why `--capture` now snapshots the roster alongside the raid.)

### The raid does not end when the quest does (2026-08-30)

Completing the objective raises a banner offering **Return to Hamlet** or
**Continue Raid**, and waits. `run.py` printed `no room advice to act on` at
the one moment the room genuinely had nothing left to advise, and every live
run stopped there -- including the 2026-08-19 one that reached Quest Complete.

`RoomAdvice.raid_end` decides it and **defaults to leaving**. Everything still
on the map is optional by definition once the quest is won, so continuing
spends real risk on loot while returning banks a completed quest. The
objective being met is `RoutePlan.targets` being empty, never the printed note
read back. A "continue" needs two things at once: something priced positive
still reachable, and a party in shape to take it -- and the risk gate is
stress and affliction rather than HP, because `LiveHero` carries no `max_hp`
and a bare HP number cannot be made a fraction there.

**`_end_raid` requires the SAVE and the SCREEN to agree before it clicks.**
`run.py` decides from the save; the actuator checks `screen.recognise` is
showing `quest_complete`. Either alone is not enough -- the save says nothing
about which banner is up, and a screen match alone would hand the decision to
a hash. Both buttons are irreversible in opposite directions, so an
unrecognised screen refuses rather than clicking where the button usually is.
It is confirmed by the banner CLEARING, not by a save write: the button is a
UI transition and demanding a save change would read the ordinary success as a
failure, which is the `_curio_active` trap.

After it, the results chain is three presses of one button:
`raid_results` (Victory / Quest Rewards) -> `raid_progression` -> the same
page again once the quirk rewards animate in -> the Hamlet.
`layout.RAID_RESULTS_NEXT` carries both "Next" and "Return to Town", because
it is the same bottom-centre control relabelled per page.

### Locating a HUD button is a command now (`tools/find_button.py`)

`PARTY_ORDER` and the pack grid are the two points in `layout.py` that were
measured rather than hand-clicked, and both were measured once by hand with
only the answer written down. The tool alpha-matches a shipped asset against a
live frame over a scale sweep and reports the centre as a fraction of the
client rect.

**The check that it measures what it claims**: it recovers PARTY_ORDER's own
fraction -- (0.8629, 0.7712) from a 2560x1440 capture and (0.8629, 0.7715)
from a live 1280x720 frame, against the hardcoded (0.8629, 0.7715). Same
fraction, two resolutions, to within a pixel, having been told nothing.

Diagnostics only: it imports OpenCV, which nothing under `bridge/` may depend
on. The output is a fraction to paste into `layout.py`, so the runtime still
needs nothing but numpy -- the same split Pillow already has.

Not everything can be matched this way. `RAID_RESULTS_NEXT` is drawn as text
by the base game (the only `raid_results*.png` in the install belong to the
Arena DLC), so it is measured off a live frame by **hue** rather than
brightness: the panel frame directly above the label is bright too, and a
luminance threshold lands the click on it.

### A capture session no longer needs a code edit

`build_refs` named references through the hardcoded `CAPTURE_NAMES` prefix
map, so adding a screen meant editing `screen.py`. That is why the front end
went unmodelled for months while the screens sat one click away from a running
game: the cost was never the pixels, it was the diff.

```bash
python bridge/screen.py --capture quest_complete   # bank the live frame
python bridge/screen.py --calibrate                # fold it in, then --check
```

Anything under `captures/screens/<name>.png` is a reference called `<name>`.
**`--capture` REFUSES a frame that lands within `MARGIN` of a different
screen**, which is not caution: `recognise` demands the runner-up be `MARGIN`
clear, so banking a colliding frame turns the screen it collides with into a
permanent UNKNOWN. It is the recogniser's own near-tie call, moved one step
earlier, and it earned its place on its first live use by refusing to bank the
title screen under the name `intro_text`.

Six screens came out of one live boot: `quest_complete`, `raid_results`,
`raid_progression`, `raid_progression_quirks`, `town`, and **`pause_menu`** --
which is not on the path at all and is the most important of them, because ESC
opens it and it carries *Exit to Desktop*.

Worst separation over the corpus is **0.432** now, down from 0.484. The new
closest pair is the two progression states, which are one page before and
after the quirk rewards animate in; both are kept because the page has to be
recognised in either state and takes the same button.

### The TOWN'S GEOMETRY IS SHIPPED DATA (`bridge/townlayout.py`, 2026-08-31)

**Do not measure a building panel off a screenshot.** Darkest Dungeon ships
its complete UI layout as `.darkest` **block-layout** files, in a **1920x1080
design space**, and this repo's own `DarkestParser.parse_blocks` already reads
them. ~25 files under `campaign/town/`, one per building, plus `shared/` for
the dialogs and the inventory grids.

The decisive one indexes every building's entry button by name:

```
campaign/town/building_navigation/building_navigation.layout.darkest
  building_navigation_layout: .base_size 128 1000
                              .button_start_position 0 0 .button_spacing 0 68
  stage_coach 0  blacksmith 1  guild 2  camping_trainer 3  tavern 4
  abbey 5  sanitarium 6  nomad_wagon 7  graveyard 8  statue 9
```

So "map out the coordinates for the other nine buildings" is one parser call,
not nine calibration sessions -- **for everything except the button that opens
a building, which is not on the Hamlet at all.** See "The strip is not on the
Hamlet" below; that one was settled by a live run, not by the files. `design_pt(x, y)` is the whole bridge between
that space and the client-rect fractions `bridge/layout.py` already speaks, and
`ResolvedLayout.at(frac)` is the single seam that applies the letterbox
correction -- so a derived point gets the identical treatment as a hand-clicked
rank point.

```bash
python bridge/townlayout.py        # print every derived point + the cross-checks
```

**THE CROSS-CHECKS ARE THE EVIDENCE, and they are tests.** A derivation cannot
check itself, so it is compared against the three coordinates in
`bridge/layout.py` obtained some other way entirely
(`tests/bridge/test_townlayout.py`):

| what | measured | derived from the files |
|---|---|---|
| `MODAL_OK` | (946.5, 584.2) design px, edge-detected | `confirm_dialog`: **(948, 589)** -- 1.5 px x, 4.8 px y |
| raid-pack column pitch | 106.66 px of 2560 = **80.0** design | `inventory_item_layout.icon_size` 72 + 8 gutter, AND `provision_store_grid_layout.offset` **80** |
| `TOWN_CLOSE_PANEL` | (0.7874, 0.1467), isolated by hue | `activity_log_pos (144,132)` + `close_pos (1352,12)` + half a glyph -- **exact** |

The third is the one that matters most: it is a **three-term composition**
(screen -> panel -> widget), which is the rule every accessor depends on and
which the other two do not exercise.

**ANCHORING IS PER WIDGET and has to be ESTABLISHED, never assumed.**
`confirm_dialog_layout.base_pos` is `960 200` -- exactly half of 1920 -- so
that panel is centre-anchored in x. A close glyph is not: the same comparison
says `close_pos` anchors its TOP-LEFT, which is where `CLOSE_GLYPH_CENTRE`
(15.8, 14.4) comes from. Clicking a corner is how a click misses a button the
log then says was pressed.

Two more rules, both of which cost something:

- **A missing file yields no points, never a guess.** `_blocks` returns `{}`
  and every accessor returns `None`; `_add` refuses a composition if ANY term
  is missing. Treating an unreadable offset as (0, 0) does not produce no
  point -- it produces a point in the wrong place, which is the one outcome
  layer 3 must never have.
- **A NEGATIVE index is a real point, one row above the panel.**
  `start_pos + spacing * -1` is perfectly valid arithmetic and every accessor
  returned it happily; `recruit_slot(-1)` produced a plan instead of a
  refusal. `_index_ok` guards all ten accessors and rejects `bool` explicitly,
  because `True` is an int and `exchange_amount(index, up)` is exactly the
  argument pair to get the wrong way round.

### WHERE THE DERIVATION STOPS: the front end is CANVAS space (2026-08-31)

The town's layout files are screen-space and compose cleanly. **The front end's
do not**, and the difference is not cosmetic -- `fe_flow/fe_flow.layout.darkest`
is positioned in a tall scrolling canvas that a camera pans over:

```
fe_flow_positions: .ruins_center 0 -1080  .insanity_center 0 -2360
                   .saves_scroll 0 -1520  .savegame_frame 350 1950
                   .save_slot_window_position 200 548
                   .save_slot_window_size 1800 434
                   .scroll_bar_position 1500 580  .scroll_bar_height 376
```

`ruins_center` and `insanity_center` are a screen apart in y (-1080, -2360) --
those are camera stops on a canvas, not points on a display. So a coordinate
here cannot be turned into a click without the projection, and the numbers do
NOT reproduce the measured save-row constants: `savegame_slots_offset` says
144 against a measured pitch of 136.5 design px, and `save_slot_window_position`
548 against a measured first row at 791.

**So `bridge/townlayout.py`'s method is for the TOWN, and the front end stays
measured.** `SAVE_ROW_*` and the `MENU_*` points were edge-detected off
captures and must stay that way. Do not "improve" them from these files.

**A HALLWAY HAS NO DIRECTION OF ITS OWN -- Fran, 2026-09-03. APPLIED.**
*"hallways have no directions, only left (backtrack) and right (advance) this
should already be covered somewhere"*. `D` advances and `A` backtracks in
every hallway, because DD draws the party entering at the LEFT of the hallway
art however that corridor runs on the map.

**It was already covered, twice, and both were being outvoted.**
`act.plan_hallway_sweep` has carried his words since it was written -- "a
hallway is entered at its LEFT and left at its RIGHT" -- a hundred lines from
`Walk.heading`, which said the opposite. And the corpus measurement that was
taken as grounds for a CORRECTOR actually refutes the whole idea: the tile
order is monotonic in `mappos` but runs **both ways** -- 25 corridors
ascending, 23 descending, and **23 of the 48 vertical**, carrying no screen
left/right at all. A quantity that is undefined for half the corpus is not a
noisy measurement of the walk direction; it is not a measurement of it.

**The live log says the same.** In `coEF` the party pressed `A`, crossed the
entire corridor, and came out in `rooF` -- the room it had come FROM -- and
`D` then took it to `rooE`. `corH` before it: the save said `A`, the entrance
was on the LEFT, and the party oscillated between `rooH` and `corH` for 180 s.

**Four mechanisms had grown to correct a direction that was never in doubt,**
and they are all gone:

* `Heading`, a global bit flipped on arriving somewhere unexpected;
* `heading_wrong`, a per-corridor "the save was wrong here" set, needed only
  because `_forward_key` re-asserted the measurement on every call;
* a stall-driven flip in both walk loops, spending the first stall budget on a
  direction hypothesis before blaming an obstacle;
* a walk-back on an exhausted budget, re-crossing the whole corridor.

What replaces them is a derivation with nothing to learn. `A` is asked for in
exactly two places: `Walk.key_toward(index)`, when a named tile is closer to
the door than the party is, and `_walk_toward`, when `_target_end_index` puts
the destination at the end the party came in by (`Walk.entry_index`). Anything
else, including anything unreadable, is `D`.

**Why a stall no longer means "maybe the wrong way".** A backward step is
INVISIBLE in the save -- per-tile `knowledge` and `light` are effectively
monotonic, 108 growth events across the corpus against 2 that ever shrank --
so a party walking the wrong way writes nothing and reads exactly like a party
walking into a wall. That indistinguishability is what justified the flip.
With the direction derived, only the obstacle is left, and the loop says so
and stops instead of re-crossing the corridor to find out.

**The three tests that blocked this were synthetic, not capture-derived**, and
that is the part worth carrying forward: they built a `Walk` by hand and
asserted what `Walk.heading` would return, so they could only ever agree with
it. A test written from the same belief as the code is not independent
evidence for it, however much prose it carries. Fifteen tests in that family
were rewritten or retired.

**`_send_and_confirm` PRODUCES FALSE NEGATIVES, and they are most of what
looked broken tonight (2026-09-03).** It acknowledges an input by watching
`(mtime, size)` of `persist.raid.json` for 15 s. Two actions were reported as
"the input did not land", three attempts each, while both had plainly worked:

- **The map click.** Driven by hand at an offered cell, `area_tile` went 1 to
  0 within two seconds -- the party moved -- and the same click through the
  actuator reported no save change.
- **The torch relight.** Reported as failing three times in a row, and over
  the same stretch the pack went **9 torches to 6** and the light went
  **70 to 96**. Three relights landed while the log said none had.

So the save is not a per-action acknowledgement. DD writes the raid file on
its own schedule -- an area change shows up at once, and other actions do not
-- and 15 s of silence means nothing about whether an input arrived. Loot IS
confirmed reliably, so the channel is not dead, only unreliable per action.

**This is why the layer above it kept being blamed.** A false negative here is
indistinguishable from a bad coordinate, so the map fit was tightened five
times tonight chasing clicks that had already landed. Anything that reads the
save as proof of an input needs a second opinion: the torch has a drawn meter,
the pack has a drawn count, and `screen.what_appeared` reads either without
knowing what they say.

**THE MAP LAYER SHOULD BE REBUILT ON SHIPPED ART AND ON THE TOOLTIP -- Fran,
2026-09-03.** Three things he supplied in one exchange, and each replaces
something the current layer guesses at.

**1. The icons ship, so a cell does not have to be inferred from edges.**
`panels/icons_map/` holds the exact art: `room_empty`, `room_battle`,
`room_curio`, `room_treasure`, `room_entrance`, `room_boss`, `room_unknown`,
and separately `hall_clear` / `hall_dim` / `hall_dark` / `hall_door`, plus
`marker_battle` / `marker_curio` / `marker_trap` and `indicator`. **Rooms are
64x64 and halls are 24x24**, so the room:hall ratio is a fixed 8:3 at every
zoom -- which is the room-vs-hall discriminator `_cells_for` currently
approximates with `MIN_CELL_UNITS` against a single scale. Same principle as
the town layout: the game ships its own geometry and measuring it again is the
worse of the two options.

**2. The TOOLTIP is the permission, and it is a positive one.** Fran: *"if you
don't get the tooltip the game won't let you go to that path"*. Hovering a
legal destination draws "Move to this room"; hovering anything else draws
nothing. That is a far better acknowledgement than the current one -- click,
then wait 15 s for a save change that never comes -- and it is the same
positive-evidence rule the rest of the bridge already follows. The flow it
implies is: fit the map, HOVER the candidate, look for the tooltip, and only
then click.

**One caveat measured while looking at it:** the tooltip is drawn OVER the map
and occludes cells beneath it, so the fit has to be taken BEFORE the hover,
never during.

**3. The graph is the real goal.** Fran: *"a way to detect the paths... detect
on the map the starting rooms and ending of hallways as well as a way to
connect those in a graph like matter"*. With rooms and hall tiles separable by
their own art, and `hall_door` marking where a hallway meets a room, the panel
yields rooms as nodes and hall runs as edges -- read off the SCREEN rather
than fitted to the save. That removes the whole class of failure this session
kept hitting, where a transform is confident and wrong.

**Why it is needed: MINIMUM ZOOM BREAKS THE CURRENT DETECTOR.** Fran wants the
view always at minimum zoom, which is right -- it is the only view where the
whole map is present. Measured there: rooms come out 16-19 px and halls 5-12,
and `find_boxes` MERGES vertically adjacent cells, reporting 16x30 and 19x30
where two stacked cells sit. Nine of sixteen boxes then match and `explained`
lands at 0.56 against a 0.75 bar, so the fit is refused on the very view that
shows everything.

**And the target the fit missed was the obvious one.** With the cursor parked
on the cell the party should move to, it sat at client (877, 620) inside an
18x18 box the fit did not explain -- one of the seven it dropped.

**THE WHEEL DESTROYS THE MAP AND `TAB`/`TAB` DOES NOT BRING IT BACK
(2026-09-03).** Fran: *"with the mouse wheel you can also zoom in/out"*, and
separately *"if you drag you lose the map"*. Both halves are now measured, and
the second is worse than it sounds.

Wheeling over the panel was tried as a way to reach a known extreme -- the
trick that fixed the save list and the roster. It does not work here. Six
notches one way took the panel from a clean **20.0 px/unit, 13/13** fit to
**zero boxes detected**; twelve notches back did not recover it, nor did eight
more the other way, nor did closing and reopening the panel. The map was
restored only by restarting the game.

**So `_reset_map_view`'s premise is FALSE.** Its docstring says closing and
reopening "lands on the same whole-map view every time"; it does not once the
view has been wheeled off. `TAB`/`TAB` resets a panel that is merely zoomed,
not one that has been lost. Nothing in the bridge wheels the map, and nothing
should -- `SendInputBackend.scroll`'s own note reached the same conclusion from
one direction and this is the second, from both.

Worth knowing about that API: `scroll(at, notches)` uses `notches` only for
its SIGN and always sends exactly one notch, so a caller asking for ten gets
one. That is not a bug anything depends on, but it is why an early attempt at
"zoom all the way out" looked like a no-op.

**THE MAP PANEL EMPTIES ITSELF, AND THAT IS THE OPEN ONE (2026-09-03).**
After the five fixes below, leaving `rooF` for `rooE` measures a **9/9** fit
and clicks (892, 633) -- within 2 px of the point that landed successfully
earlier in the same session -- and the save never moves. The banked frame
(`captures/map_panel_blank_after_fit.png`) shows why: the panel is drawing an
EMPTY GRID, no rooms at all, so the click went onto bare ground.

So the sequence is: the panel draws the map, the fit is measured correctly, and
by the time the click lands the panel has blanked. Sampling more frames does
not help -- the fit chosen is the best of six taken over 1.2 s, and it is the
*panel* that changes afterwards, not the measurement that was wrong.

**What is NOT the cause**, each ruled out by a fix that is now in and tested:
the transform (9/9 explained, 3.7 px worst), the scale search (`_refine` finds
the between-candidate truth), the ranking (`_rank` no longer prefers reading
the map as tiny), a stale planning frame (re-measured immediately before the
click), and a stray curio panel (closed at the top of every room).

**Where to look next.** Two candidates, and they are distinguishable: the
`TAB`/`TAB` reset in `_reset_map_view` may still be easing when the click is
sent, or a click that lands on empty grid may itself PAN the panel, in which
case the first miss is what causes the second. Watching the panel across the
whole send-and-confirm window -- rather than only before it -- would separate
them, and neither needs a change to the fit.

**A MAP CLICK IS MEASURED ON ONE FRAME AND LANDS ON ANOTHER (2026-09-03,
fixed).** Leaving `rooC` for `rooF` in a live Warrens raid clicked client
(929, 602) three times and the save never moved.

**The first reading of this was wrong and is worth keeping as the warning.**
The router printed `6/7 boxes explained` where the raid that worked printed
`6/6`, and the banked miss frame showed blank map under the crosshair -- so it
was filed as "the destination is unexplored and has no box". It is not.
Re-fitting the map frame banked one minute later gives **7/7 boxes explained**
and puts `rooF` at (928, 603): the click point was right, for a view that was
no longer on screen when it was sent.

`_reset_map_view` presses TAB twice and scans for `MAP_RESET_WATCH` seconds,
keeping the frame with the most cells behind a successful fit -- and the panel
is still animating toward a settled view while that scan runs. So the point can
be seconds stale, and a stale point does not fail loudly: it lands wherever the
map draws that spot now.

The target is therefore re-located on a fresh grab immediately before the
click, and the fresh fit wins. A fresh view that will not fit is a REFUSAL
rather than a reason to fall back on the stale point, because a view that
cannot be fitted at the moment of the click is exactly the view that swallows
one -- and a refusal keeps the turn re-offerable where a spent click does not.

**The transferable part: `N/M boxes explained` is a statement about the VIEW,
not about the map.** A dropped box means the frame is mid-animation at least as
often as it means a room is missing, and the two call for opposite responses.

**THE ROW IS THE SLOT, and that was confirmed by scrolling rather than
inferred (2026-09-03).** With `profile_0`, `profile_1` and `profile_8` on disk,
the bottom of the live list reads "Click the letter to begin...", "Click the
letter to begin...", "Darkest - Hamlet, Week 6" -- slots 6, 7 and 8. **Every
free slot draws its own letter row**, so holes are not closed up and
`profile_8` is on row 8. The alternative reading -- a COMPACT list, one row per
campaign -- fits every outcome the boot chain had produced until then, was
adopted for one afternoon, and created a campaign nobody asked for by clicking
slot 1's letter while aiming at `profile_8`. **One capture was worth more than
every inference from behaviour**, which is the transferable part.

**The save list scrolls, and it is DRIVEN now (2026-09-03).** This paragraph
used to end "until then, `--slot N` and deleting a campaign by hand are the two
ways through". It was measured the way it said to measure it, off the live
list, by reading each visible row's info column -- only a campaign draws
location / week / date, so the pattern identifies the scroll offset directly
instead of assuming a rows-per-click:

    clicks 0 -> CAMPAIGN CAMPAIGN   (slots 0, 1)
    clicks 1 -> letter   letter     (slots 2, 3)
    clicks 2 -> letter   letter     (slots 4, 5)
    clicks 3 -> letter   CAMPAIGN   (slots 7, 8)  and unchanged through 12

**The arrow PAGES by the viewport and CLAMPS at the bottom.** Both halves are
load-bearing: paging keeps the click count small, and clamping is what makes an
over-estimate harmless. At the bottom the rows sit on the existing grid, so
`save_row` needed no new geometry. `menus.scroll_plan` is the arithmetic and
`overnight._click_save_row` drives it.

**The scroll is measured and the click is CHECKED**, which is the half that
matters: the geometry comes from one machine at one resolution and its failure
mode is not an exception, it is clicking a different campaign and playing it.
So the row's own content decides, and a row that is not a campaign is refused
-- because clicking an empty letter does not fail either, it STARTS one.

`total_save_rows` counts to the highest CAMPAIGN, not the highest directory:
`profile_9` here is the Butcher's Circus multiplayer profile (no
`persist.game.json`) and DD draws no row for it, so counting it added a row
that does not exist and moved every target by one.

**`building_close` is confirmed on a real frame**, not just numerically: drawn
over `captures/screens/town.png` the derived point lands exactly on the
activity log's red X. That is the three-term composition
(`town_screen_layout.activity_log_pos` + `activity_log_layout.close_pos` +
`CLOSE_GLYPH_CENTRE`) landing on the pixel it predicts.

**The navigation strip is NOT drawn in either banked Hamlet frame**, and
`town_screen_layout` carries `button_navigation_offscreen_pos -40 230` beside
`button_navigation_pos 70 230` -- so the strip has a hidden state and
`plan_open_building` may be clicking a widget that is not there. That is the
first thing to check on a live Hamlet, and the reason the town layer is not
yet claimed as verified.

### Deciding a town visit: `bridge/townplan.py` (2026-08-31)

The layer beside `route.py` and `curios.py` -- it answers *what should happen
in town* and never clicks anything. Three decisions in dependency order:
**trade heirlooms -> buy upgrades -> recruit**, because a trade pays for an
upgrade and `numrecruits` changes how many heroes the coach offers.

```bash
python bridge/townplan.py --profile <dir>       # decide only, print the plan
python bridge/town.py --coach                   # plan it against the live game
python bridge/town.py --coach --act             # and do it
```

**"Crests for Scrolls" decoded, and the arithmetic is exact.** There is no
scroll currency: the four heirlooms are bust / portrait / deed / crest and the
**deed** icon is a scroll. `crest 3 -> deed 1` is shipped. A new campaign
starts `bust 10, portrait 10, deed 10, crest 20`; `stage_coach.numrecruits a`
costs deed 3 + crest 4, leaving 7 deed; `b` needs 8 -- **one short**, closed by
exactly one trade.

**THE TRADE IS SOLVED, AND THE COST FUNCTION IS DERIVED.** The first version
counted raw units and picked `2 portrait -> 3 deed` over `3 crest -> 1 deed`
because 2 < 3. That is twice as expensive, and the game says so: the three
`X -> crest` rows price every heirloom in the finest denomination --
**bust 1.5, portrait 3.0, deed 1.5, crest 1.0** -- so the "cheaper" plan spent
6.0 against 3.0. Corrected, the solver reproduces Fran's own instruction.
*An expert's instruction and a data-derived objective agreeing from opposite
directions is much better evidence than either alone.*

**`persist.upgrades.json` IS NOT SHAPED LIKE ITS FIELD NAMES.** Two of the four
fields on every `purchases.<n>` entry decode to **nothing**, so the obvious
reading -- filter on `is_purchased`, key on `requirement_code` -- returns an
empty set from a save with 228 purchases in it. What it really encodes:

- **`tree_id` is `dd_name_hash(tree.id)`**, unsigned (DSON reads 4-byte fields
  signed). 48 of 85 distinct hashes invert against the shipped `upgrades/**`
  trees; the rest are DLC and Workshop classes whose files are elsewhere.
- **An entry's PRESENCE is the purchase, and the COUNT is the rank.** Five
  entries for `(crusader.smite, hero 11)` is that skill at rank 5. Verified:
  **0 of 85 exceed their own tree's rank count** over 228 entries, which is
  what makes it a reading rather than a guess.
- **`instance_number` is WHICH HERO.** One tree appears once per hero that
  owns it -- Fran's save has two Crusaders at ranks 1 and 5.
- **No BUILDING tree appears in either of Fran's saves**, and that is not a
  format gap: the Week 22 wallet is within two busts of the shipped starting
  heirlooms, so none was ever bought.

**Recruiting is a SHAPE question, and the obvious metric has NO GRADIENT.**
Scoring a hire by "how many curated comps can the roster field afterwards"
returns **zero for every candidate** from the two-hero tutorial party, because
no four-hero comp is fieldable from three heroes -- all 179 shapes score 0 and
the choice silently collapses onto the tie-break. `reachable(shapes, have,
budget)` is what has a gradient: with two hires in budget the same roster has
**9 shapes within reach**, and the classes that close the distance are exactly
the ones a Crusader/Highwayman core wants (Vestal, Plague Doctor, Occultist,
Man-at-Arms, Houndmaster, Grave Robber all tie at the top).

**And hires INTERACT, so the SET is searched, not the hires one at a time.** A
comp is a class multiset; the best single hire followed by the best remaining
one can leave a pair that completes nothing while another pair completes a comp
outright. The coach shows four to six, so `C(6,2)` is fifteen and the exact
answer is simply cheaper than arguing about a heuristic. Greedy remains only as
a fallback for an implausibly long offer list.

**`rostersize` is deliberately OFF the default upgrade path, and that was
measured.** With it second in the priority the solver buys its first rank too
and pays `2 bust -> 3 crest` and `2 portrait -> 3 deed` -- **9
crest-equivalent of trades** on week one, emptying the wallet the next ranks of
the top path need. `UPGRADE_PRIORITY_WITH_ROSTER` turns it back on.

### The strip is not on the Hamlet -- a live run settled it (2026-08-31)

`building_navigation.layout.darkest` indexes all ten buildings and
`townlayout.building_button` derives each one correctly. **The strip is drawn
INSIDE a building panel**, as the way to hop between buildings; from the town
screen you click the building in the scene.

The first live town visit is what established this, and it is worth the detail
because the failure was silent in exactly the way this bridge is built to
prevent:

```
[town] open the stage_coach      CLICK 181,291
[night] OK          open the stage coach
[night] UNCONFIRMED numrecruits.a      (the wallet did not move)
[night] UNCONFIRMED 3 crest -> 1 deed  (the wallet did not move)
[night] UNCONFIRMED hire plague_doctor (the roster did not grow)
```

The click resolved correctly (client origin 92,115 plus the derived 89,176)
and landed on empty sky. Six more clicks then went into an open Hamlet.
**Every one came back UNCONFIRMED**, nothing was claimed as done, and the loop
embarked and carried on -- which is the save-acknowledgement design and the
never-fatal rule both doing their job on the first night they were used.

**`BUILDING_SCENE_POINTS` is MEASURED and holds one building.** The x is
derivable: hue-detecting the Hamlet's red banners (the method that measured
`TOWN_CLOSE_PANEL`) puts the stage coach at design x **313.9** against a
shipped `stage_coach_layout.pos` of **315**, and the statue at **939** against
**940**. The y is not -- fitting the stage coach and the graveyard predicts
the statue 23 px out and the nomad wagon 146 px out, because the scene is a
perspective projection with a per-building z and `alert_offset` is in units of
its own. So the y is measured off a live frame like every other screen
coordinate here, and the other nine buildings are **absent rather than
guessed**: `plan_open_building` refuses for them. Each needs a decision rule
before it needs a coordinate.

**The guard that should have caught it, and the polarity bug in it.**
`_panel_is_open` was added before this run for exactly this risk, and it let
the clicks through: it read `recognise(img) not in TOWN_SCREENS`, so an
unrecognised frame counted as "opened". **A one-sided test has to say NO when
it cannot see.** It now returns True only for a screen in `BUILDING_SCREENS`,
which is empty until an interior is banked -- so it refuses, banks the frame
(that frame is how the interior gets its reference), and still clicks the
close, because "did not open" and "opened but unrecognised" are the same
observation and a panel left over the Hamlet swallows the embark walk.

### Inside the panel, measured off a real frame (2026-08-31)

The corrected scene click opened the stage coach, the guard could not confirm
it (nothing banked yet), and it banked the frame -- which WAS the open panel.
`captures/screens/stage_coach_panel.png` is that frame, it separates from every
other reference by **0.700** against a 0.35 threshold, and anything under
`captures/screens/<name>.png` is auto-registered, so adding a building is a
capture plus a name in `town.BUILDING_SCREENS`.

Overlaying the derived points on it settled four questions at once:

- **The navigation strip derivation is CORRECT.** All ten markers land on
  their icons -- the strip is there, down the left of the panel, exactly where
  `building_navigation.layout.darkest` says. It simply does not exist on the
  Hamlet. So `building_button` is right and is the way to hop between
  buildings once one is open; `building_scene_point` is the way in.
- **`building_close` lands on the panel's X**, a third independent
  confirmation of `CLOSE_GLYPH_CENTRE`.
- **The recruit slots were off by a consistent (+26, +26)** at 1280x720:
  `store_item_pos` anchors the row's top-left and the portrait -- what a drag
  must grab -- sits about half an icon down and right. `RECRUIT_PORTRAIT_OFFSET`
  is that, measured, at design (+39, +39). Equal in both axes because it is
  half a square icon, which is why one constant serves every row.
- **NO UPGRADE TREE IS DRAWN ON THAT PANEL.** The derived nodes land in the
  dark left margin beside the navigation strip; the body is the coachman and
  the recruit list. The arithmetic is not the problem -- either the trees need
  the panel scrolled, or this build puts them somewhere
  `building_base_layout.upgrade_base_pos` does not describe.

**Purchases WERE gated on `townlayout.UPGRADES_MEASURED`; the gate is open.**
The flag lifted once the building crest was found to open the trees
(`plan_open_upgrades`) and all 13 nodes landed within 1 px of their box
centres on a live frame. `EXCHANGE_MEASURED` lifted with the panel, so
**nothing in a town visit is gated today.**

The two tests that pinned the CLOSED side were retired on 2026-09-03. They had
been written to skip on the flag "rather than being deleted", which reads as
caution and is not: a test that can only skip is a line in the report and
nothing else, and one of the three -- `test_a_GATED_action_is_reported_unsent`
in `test_town_visit.py` -- had its body behind the flag and so reported PASS
while asserting nothing. `tools/find_silent_tests.py` is what found them, and
the lesson is that a flag-gated test needs re-reading when the flag lifts,
because nothing goes red. Their findings live in the tests of the open path.

### Acting in a building: nothing changes the SCREEN (2026-08-31)

`town.walk` moves BETWEEN screens and knows a click landed because the
recogniser sees a different one. **Nothing inside a building panel does that.**
Buying an upgrade, trading heirlooms and hiring a recruit all leave the
recogniser reading the same panel, so a click that silently did nothing and a
click that worked are **pixel-identical**.

So `visit_stage_coach` acknowledges every action against the **save**:
a trade and a purchase by re-reading the wallet, a hire by re-reading the
roster. `Did(sent=, confirmed=)` reports the two separately, and
`tools/overnight.py` counts them separately for the same reason -- pooling them
would hide exactly the gap this design exists to expose.

- **A recruit DOES write the save**, unlike a pack rearrangement (which moves
  the item on screen with the mtime unchanged). So this drag, unlike that one,
  can be acknowledged without pixels.
- **A hire is a DRAG.** The coach panel selects on click and hires on drop, so
  a click reads as "look at this hero" and the log would show a hire that never
  happened.
- **The roster row only advances on a CONFIRMED hire**, so a failed one does
  not silently skip a slot and drop the next recruit on top of it.
- **The panel is closed even when everything failed.** A visit that leaves one
  open blocks every later screen transition, and the walker would then report
  an unrecognised screen with no idea why.
- **A town visit is never fatal to the night.** The raid is the point; a visit
  that half-worked is a normal outcome to report.

### It runs itself now: `tools/overnight.py` (2026-08-31)

```bash
python tools/overnight.py --dry --max-iterations 2       # sends nothing
python tools/overnight.py --max-iterations 200 --until 20:00
touch archive/logs/overnight/STOP                        # ask it to finish
```

Launch, boot a campaign, play the Old Road, walk to the Hamlet, provision,
embark on a generated dungeon, play that, repeat -- then delete the slot and
start again. Per-iteration logs and a rolling `summary.json` under
`archive/logs/overnight/`. **It refuses to start beside a training run or a
pytest suite**, both of which have crashed this machine.

Rules that are not preferences:

- **The game is RESTARTED between iterations, and that is required.** Fran's
  finding, reproduced exactly: deleting a save without restarting leaves DD's
  cached slot list stale, and the next campaign in that slot skips the
  difficulty screen and comes out with 0 gold and an estate named **"Click the
  letter to begin..."** -- the save-select hint text, captured as the name. A
  launch per iteration is therefore NORMAL, so the relaunch bound counts
  consecutive failures to get a window; counting launches capped the night at
  six.
- **`-resetcampaigntutorials` keeps every iteration starting in the Old
  Road.** Completing the tutorial once is permanent: DD then drops new
  campaigns into the Hamlet, which showed up as `run.py` advising on `rooA`
  from the raid template while the game sat in town with 0 gold.
- **A stall ends in 180 s, not 900.** Progress is the log growing OR the raid
  save being written; either alone would call a normal pause a stall. A frame
  is banked at the moment it gives up.
- **The raid-over sentinel is COUNTED, not searched for.** One log carries a
  whole campaign, so "the text is present" is true from the second raid onward
  before the second raid has started.
- **`to_title` waits for the game to ARRIVE.** A freshly launched DD spends a
  while on logos and a loading screen that match no reference, and ESC into
  that does nothing.
- **Never `stdout=PIPE` on the play phase.** Nobody drains it, it fills at
  ~64 KB, and `run.py` blocks on its next write looking exactly like a stuck
  bridge. It writes its own `--log`; stdout is a duplicate.

### Tearing a campaign down: `bridge/reset.py`

`bridge/menus.py` is the forward flow; this is the reverse.

**The delete is by PATH, and its safety is a TYPE rather than a check.**
`delete_owned` accepts only an `OwnedSlot`, and the only constructor is
`claim_slot`, which refuses a directory that already exists -- so a save on
disk before the process started cannot be *represented*, let alone deleted.
Clicking the red X is still refused, for the reason it always was: two visible
rows, a real campaign on row 1. **Proposed again on 2026-09-01 and declined
again** -- the X is positional, `profile_0` sits on row 1, and a misclick
deletes a real campaign irreversibly. When a leftover genuinely has to go, a
by-path removal of a directory identified by its CONTENTS (a tutorial-only
progression: `total_quests_finished` 1, `total_recruited_stage_coach_heroes`
0) with the game closed is strictly safer than aiming at a button, and it
leaves `profile_0` untouchable rather than merely unaimed-at.

**A crash must not end the night.** The strict rule blocked every run after
the first crashed iteration. Claims go to a journal OUTSIDE the game's save
directory and an existing slot may be ADOPTED when the journal says this tool
created it and never released it. A human's campaign has no entry; a delete
releases its claim; an entry expires.

**THE TEST SUITE USED TO DESTROY THAT JOURNAL (fixed 2026-08-31).**
`tests/bridge/test_menus.py` overrides `save_root` with a `tmp_path` but most of its
calls do not override `journal`, so with a fixed default **every suite run
wrote about twenty claims about pytest temp directories into the production
`archive/reset_claims.json`** -- and `JOURNAL_KEEP` (50) then evicted the real
ones. Found the hard way: after four suite runs in one session the production
journal held nothing but `pytest-of-Francisco` paths, and `tools/overnight.py`
could no longer adopt the very slot it had created that morning. The loop
refused to start, correctly, **on evidence the test suite had deleted**.

`journal_for(save_root)` is the fix and it is structural rather than a
convention: the production root resolves to `CLAIM_JOURNAL`, any other root
gets a journal inside itself, so a claim about another world cannot be written
into this one's journal even by a caller that forgets the argument. It goes
INSIDE the given root and not beside it -- `root.parent` is pytest's shared
`tmp_path` base, which would share one journal between tests and between xdist
workers and lose the isolation one level up. Three tests pin it.

**CLOSE STEAM BEFORE AN UNATTENDED RUN.** Steam Cloud is active for this app
and it restores a slot the tool has just deleted -- observed 2026-09-01: the
loop deleted `profile_1` at 00:10, the directory was back at **00:11:01**, and
`claim_slot` then refused it forever because the journal entry for that path
had already been marked released. So the night stalls on a leftover nobody
created. `delete_owned` anticipates the restore (`CLOUD_RECHECK`) but the
window is short and Cloud came back after it.

**This is a real gap in the ownership model, not just an operational note.** A
Cloud restore of our own just-deleted save is, to the journal, indistinguishable
from a human creating a campaign at that path -- the entry says released, and
"released" is exactly what makes a slot unownable again. Widening adoption to
cover it (a recently-released claim plus a directory whose mtime is after the
release) is defensible and narrow, but it IS a weakening of the one invariant
that makes `delete_owned` safe, so it is written down here rather than done
quietly. With Steam closed the delete stuck on the first try.

**A leftover the tool cannot prove it owns is not a reason to delete
anything.** When that state is reached -- and it will be, because it is what a
crash plus an expired claim looks like -- point the runner at a free slot with
`tools/overnight.py --slot N`. Hand-deleting the directory or hand-writing a
journal entry both defeat the invariant; the invariant is worth more than the
convenience, and a free slot costs nothing.

**Exit to Main Menu is one row above Exit to Desktop**, 37.5 px at 720p, and a
blind click on a screen that merely resembles the pause menu ends the night
rather than the iteration. Both are banked -- the second deliberately
unreferenced, like `TORCH_METER` -- so the gap is asserted, and a test greps
the package to prove nothing acquires a caller for the wrong one.

### Between raids: `bridge/town.py`

```bash
python bridge/town.py --where            # which screen, and what it would do
python bridge/town.py --to-town --act    # raid results -> the Hamlet
python bridge/town.py --embark --act     # Hamlet -> provisioned -> in a raid
```

**DD draws ONE action control at the bottom centre and relabels it per
screen** -- Next, Return to Town, Embark, Provision, Embark -- so this is a
table from a recognised screen to what that button means there, not six
calibrations. Anything not in the table refuses: these screens sit over the
world and over a save list.

- **A click can be swallowed by an animation.** The progression page reveals
  quirk rewards over ~2 s; the same click that failed worked a moment later
  (quirks -> town in 3.6 s). The walk retries the same move, bounded.
- **The activity log is a screen to CLOSE, not a destination** -- it is drawn
  over the Hamlet on arrival and covers the Embark button. Its X is isolated
  by HUE; a luminance threshold finds the panel's roses instead, and of the
  two red glyphs on that row the LEFT one is the X.
- **Provisioning happens ON the provisioner**, not before the walk, and its
  purpose is to PREVENT a dialog: DD warns below eight food on a short quest,
  in its own words. What a party should actually carry is `embark.py`'s
  question, decided by simulation.
- **Telling modals apart is not the job; knowing one is up is.**
  `_modal_family` ignores MARGIN, which is safe ONLY because every dialog's
  first option is the same fixed point. Do not reuse that shape elsewhere.

### Drag (`Step(kind="drag")`)

**The motion between press and release IS the mechanic.** SDL decides a drag
happened from the motion events in between, so press-jump-release is commonly
read as a plain click on the source -- in a roster panel that means "select
this hero" instead of "move this hero", a wrong action that looks like a
working drag in the log. Twelve hops, a dwell at each end, positioned through
the verified `move`. A zero-length drag is refused.

**A pack rearrangement does NOT write the save.** Verified live: the item
moved on screen exactly as asked while the save's mtime was unchanged and its
slot index still reported the old cell. So the usual "the save changed"
acknowledgement cannot confirm a drag -- the proof has to be pixels.

### A GENERATED dungeon is not the Old Road (2026-08-30)

Everything above about dungeons was learned from the tutorial, whose map is
hand-authored. The first real Ruins quest broke three assumptions in ten
minutes, and each was invisible because the Old Road happens to satisfy it.

- **The corridor NAME is only the endpoint pair in the tutorial.** `coAB` is
  really `rooA`+`rooB`; a generated dungeon numbers its corridors `corA`..
  `corH`, and the same split invents a room `roor` and hangs all eight off it.
  `path()` plans over that, so **every route on every real raid was computed
  against a fabricated topology**. `DungeonMap.graph()` derives connectivity
  from `Tile.mappos` now -- MANHATTAN distance 1 from a corridor end to a room,
  so a diagonal room across a corner does not count -- and falls back to the
  name only when tiles carry no coordinates. Geometry AGREES with the name
  wherever the name was right, which is why the tutorial is untouched.
- **The tile order is not the render order.** `Walk.heading` reads `A` for a
  party that entered at the far end, which held over every `coXY` capture and
  is wrong on `corH`: tiles (1,16)..(1,21), party at index 5, entrance drawn on
  the LEFT, forward is `D`. `Heading` exists to correct this by walking and
  flipping and **could not**, because `_forward_key` reasserted the save's
  heading on every call and undid the flip before it was used. The party
  oscillated between two areas for ten minutes. `ActRunner.heading_wrong` now
  records the corridors whose measurement has been falsified -- per AREA,
  because `Heading` is one global bit -- and a step meant to go deeper that
  instead ended the area is what falsifies it.
- **The map panel keeps its zoom and pan, and reopening it is an ANIMATION.**
  The same save, seconds apart, drew 8 cells and then 2, with the knowledge
  bytes identical. `TAB` away and back resets it, but the whole map is only on
  screen for about a second and a half:

  | t | cells | t | cells |
  |---|---|---|---|
  | +0.01s | 2 | +2.06s | 8 |
  | +0.52s | 5 | +2.57s | 5 |
  | +1.03s | 8 | +3.08s | 2 |
  | +1.55s | 8 | | |

  So `_reset_map_view` walks frames rather than sleeping, and **keeps the
  MOST-SUPPORTED success**. Both simpler rules were tried and both failed
  live: the widest frame fitted at 8.8 px/unit on 6 of 8 boxes and predicted
  the destination outside every detected cell, and the first successful frame
  resolved while still zoomed in, where **5 cells admit a fit that is
  self-consistent and wrong**. Only a FIT failure triggers a reset -- the other
  two `locate` refusals cannot be changed by reopening a panel, and a refusal
  that quietly presses keys first is not a refusal.

**The wheel is not the lever.** `SendInputBackend.scroll` works and is kept,
but scrolling is relative to wherever the view already is and has no stop, so
it zoomed the panel further in and left it stuck. That is how the panel was
found to be stateful.

### Locating a HUD button is a command (`tools/find_button.py`)

`PARTY_ORDER` and the pack grid were measured once by hand with only the
answer written down. The tool alpha-matches a shipped asset against a live
frame over a scale sweep and reports the centre as a fraction of the client
rect.

**The check that it measures what it claims**: it recovers PARTY_ORDER's own
fraction -- (0.8629, 0.7712) from a 2560x1440 capture and (0.8629, 0.7715)
from a live 1280x720 frame, against the hardcoded (0.8629, 0.7715). Same
fraction, two resolutions, to within a pixel, having been told nothing.

Diagnostics only: it imports OpenCV, which nothing under `bridge/` may depend
on. The output is a fraction to paste into `layout.py`, so the runtime still
needs nothing but numpy -- the same split Pillow already has. Not everything
can be matched this way: `RAID_RESULTS_NEXT` is drawn as text by the base
game, so it is measured off a live frame by HUE rather than brightness,
because the panel frame directly above the label is bright too.

### Live run status (2026-08-30)

Title screen -> new campaign -> the Old Road -> combat -> loot -> objective ->
Return to Hamlet -> the Hamlet, with no human input. One raid: **76 actions,
15 battle turns, 8 MATCH and 0 DIFFER**, every send confirmed against the
save. The map panel solved the room exit at **24.0 px/unit, 6/6 boxes
explained** -- a fourth zoom level, the others recorded being 48.0, 15.3 and
10.7. `town_state.py` then read the resulting town save: 5,920 gold, matching
the screen exactly, both heroes carrying their raid stress out.

Then the Hamlet -> Estate Map -> Provision -> a real Ruins quest, which is the
whole campaign loop. Provisioning is actuated and CONFIRMED THREE WAYS -- shop
stock 18->14 on both lines, inventory stacks of 4, and gold 5,920 -> 5,320,
exactly 4x75 + 4x75. The bridge then reached **rooG**, a room beyond the
entrance of a generated dungeon, which nothing had done before.

**Two things it stopped on, recorded rather than guessed.** A relight was
advised, `T` was sent, and the bridge reported "the input did not land" three
times; `torchlight.advise` gates on `torches_held > 0`, so it believed the
party had one, and by the time the state was read back the party held **no
provisions at all** with the torch at **0**. Either the pack emptied between
the advice and the send, or `LiveState.provisions` is not reporting what the
party carries -- the first is a race worth a re-read before sending, the
second means every dungeon-layer provision decision is made on an empty dict.
Not distinguishable from the log alone. And the **curio panel** still is not
found on screen: `W` from the correct tile gave `no panel on screen after W`,
and the banked frame shows the party at the far left of the corridor with the
sconce well to the right, so it is the APPROACH that needs measuring, not the
detector.

**Still to do before it plays unattended**: curio/camp/retreat, which all need the panel
located on screen rather than assumed (see the full-pack section — the button row's y flows
with content); and stack merging, which needs a click-drag. Combat, loot, movement, the
full-pack modal, the menu boot and the room exit are live — the last of those verified on
the tutorial and expected to refuse on a full-size dungeon until a paired capture exists.

## THE SAVE DOES NOT RECORD THE IN-COMBAT FORMATION (2026-09-05)

`persist.raid.json`'s `party.heroes` is the party's **marching order**, and a
position change *inside* a fight is never written to it -- not a Move, not an
enemy push or pull, not an afflicted hero's `change_pos` act-out. There is no
second field that does: the whole raid save was walked for one, and
`battle.initiative` is ordered by initiative, `battle.enemies` is the monster
side, and the roster carries no position at all.

**Proved by making it happen.** A live Crypts fight, party `[37 shieldbreaker,
15 plague_doctor]`. Swapped the two by hand through the actuator; the same save
then reported the mover's `turns_taken` advanced from 3 to 4 and an enemy dead
-- so it had certainly been written -- and `party.heroes` still read
`[2, 37, 15]`.

**What it costs is total, and it is silent.** The advisor picks a skill that is
legal from the rank it believes, `advised_button` turns it into a digit, the
game refuses the digit because that skill is greyed out for the rank the hero
is *really* standing in, the click then lands on an enemy with nothing
selected, and the save never moves. Every poll re-derives the identical plan.
Two unattended iterations died that way, six minutes each, on a Plague Doctor
whose kit launches from ranks 2-4 standing at rank 1 with all four skills
unusable. The bridge sent 25 inputs and landed 3.

**The ordering itself is right, and that had to be checked before anything was
built on it.** Over the capture corpus, 118 snapshots where a hero is due to
act: reading `party.heroes` front-to-back, every one of them has at least one
skill it can launch; reading it back-to-front, **31 have none** -- a Vestal at
index 3 of a four-hero party, handed the turn by a game that would not let her
do anything. So index 0 is rank 1. The bug is staleness, not orientation.

### The bar is the game's own answer -- `bridge/actionbar.py`

Darkest Dungeon draws an unusable skill through a **grayscale shader**, so the
discriminator is not a threshold anybody had to choose. Measured on a
Shieldbreaker at rank 1, at the six calibrated `layout.skills` points:

| slot | skill | `.launch` | drawn | saturation | p95 brightness |
|---|---|---|---|---|---|
| 1 | pierce | 3,2,1 | LIT | 0.583 | 187 |
| 2 | adders_kiss | 1 | LIT | 0.162 | 136 |
| 3 | expose | 3,2,1 | LIT | 0.351 | 139 |
| 4 | single_out | 3,2 | **GREY** | **0.000** | 56 |
| 5 | MOVE | | LIT | 0.125 | 120 |
| 6 | PASS (the X) | | LIT | 0.302 | 112 |

**Four predictions from shipped data against four icons on a real frame, and
nothing was fitted.** His Plague Doctor partner, at rank 1 with a kit that
launches only from 2, 3 and 4, drew **four** zeros. `LIT_SAT` is 0.03 -- an
order of magnitude below the dimmest lit reading and above a floating-point
zero -- and brightness is kept as a second signal with an OR, because a
monochrome modded icon could be desaturated while lit. The two have agreed on
every slot of every frame measured; the test says so, so a disagreement is a
signal rather than a silent fallback.

**When nobody is being asked to choose, the WHOLE bar is grey**, MOVE and the X
included, at p95 48 and 49. `ActionBar.waiting` reads that, and it is stronger
than anything the save offers: `enemies_first` is derived from the turn queue
and is a *prediction*, while this is the game saying it has stopped animating.

Three rules that must not be regressed:

* **`read` returns `None` when it cannot see, and `None` is "no opinion".**
  An uncalibrated layout, a numpy that will not import, a frame of the wrong
  shape -- none of those is evidence about the game, and `plan_battle` with
  `bar=None` plans byte-identically to before this existed. That is what keeps
  `--replay` over the capture corpus valid. Same one-sided rule as
  `_panel_is_open`.
* **A greyed slot refuses in `plan_one_move`, which makes it a SCREEN refusal**
  -- so the existing shortlist walk finds a skill the game will take. The
  timing refusals stay where they are and still refuse outright, because every
  candidate for a turn that is not due shares them.
* **The rescue derives its destination from the bar, never from the model.**
  The lit/greyed pattern of four skills is a 4-bit signature that `.launch`
  predicts exactly, so `rank_from_pattern` recovers where the hero really is
  and `plan_reposition` swaps it to the nearest rank it could launch something
  from. An ambiguous pattern -- a kit that launches from everywhere lights all
  four everywhere -- returns `None` and costs nothing, because such a hero is
  never stuck. A skill the store could not resolve predicts **lit**, so it can
  never be the reason a rank is ruled out.

On the frozen frame the recovery answers rank 1 for both heroes and the rescue
picks rank 2 for the Plague Doctor, which is exactly the swap that unfroze the
live game by hand.

**`_reset_formation` was already there and is not enough.** It clicks the
game's own "Default Party order" after a fight, which is the right repair
between fights; this is the one *during* a fight, where that button cannot be
used and the save cannot be trusted.

## The screen cannot answer "is a raid running"; the save can (2026-09-05)

`screen.panel()` tells the inventory grid from everything else, so **`"map"`
means "not an inventory" and nothing more**. Handed a front end it says
`"map"`, and that turned a game sitting at the title into a router complaining
it could not fit the map of a dungeon that was not on screen.

**A positive pixel test was tried and abandoned — do not repeat it in this
form.** Four scale-free features over the panel region (bright fraction,
edge-thinness, ground darkness, row variance) were measured over the 72 banked
captures on known maps against known non-maps. **Not one separates the two
groups.** A three-feature box separates the 17 hand-labelled captures and then
accepts 31 of the 72, including `save_list_scrollbar`, `_embark_modal` and
`_town_chain`. Thresholds tuned on a small label set that leak on the rest are
overfitting, not a test. The earlier attempts failed too: a title fade's strip
spread measures 4.99 against a real map's 1.78, and one reference hash cannot
represent "a map" because three dungeons sit 0.802 apart.

The question was wrong rather than hard. `panel(img, raid_running=False)`
returns `""`, and `persist.raid.json` **exists only during a raid** — which is
already how `read_live_state` decides `in_raid`. One `Path.exists()`, no parse,
so a torn save cannot make it throw. `None` is the default and is byte-identical
to the old function; the four `act.py` sites that believed `"map"` now pass
`ActRunner._raid_is_running()`.

It can only ever be STRICTER: a stale raid save reads as "in a raid" and gives
back the old behaviour, and it can never suppress a map that is genuinely up.

## Stress treatment decides — the thirteenth decision (2026-09-05)

`ASSUMPTIONS.md` counts ~19 decisions a player makes per cycle; twelve decided
before this. Stress treatment needs **no screen at all** — deciding is pure
save reading, only acting needs pixels.

Every number is shipped data, and `load_stress_treatments` already read half of
it for `retreat.py`:

| | cheapest | middle | dearest |
|---|---|---|---|
| abbey | meditation 1000g/45 | prayer 1250/55 | flagellation 1500/65 |
| tavern | bar 1000g/45 | gambling 1250/55 | brothel 1500/65 |

Two fields it did not read, both in the same files: **`slot_upgrades`** (1 slot
per activity un-upgraded, 3 at the top — six heroes a week in a fresh Hamlet,
eighteen in a maxed one) and **the Tavern**, same shape and same resource.
`gold_per_stress` is unchanged at 22.22 because it takes the cheapest rate and
the Tavern matches the Abbey, so `retreat.py` cannot move.

Rules that must not be regressed:

- **A hero marked for the party is never treated.** A hero in a building cannot
  embark, so treating one silently cancels the raid it was selected for. This
  is correctness, not preference.
- **A hero already in a building is never re-treated** — `RosterHero.building`
  is non-empty exactly then.
- **Ordered by stress descending**, which is the argument for the feature: the
  hero nearest the Resolve Check is the one an affliction is about to cost, and
  `--affliction-penalty` measured afflictions as largely unpreventable once a
  raid is under way. The town is where they are actually prevented.
- **Cheapest slot first**, so gold buys the most HEROES rather than the most
  stress. An untreated hero at 90 is a coming affliction; a treated one at 45
  is not.
- **Planned LAST in `advise`**, out of what purchases and hires leave. A hire
  outvalues a treatment — the roster is what makes a comp fieldable at all, and
  an untreated hero is still playable while a missing class is not.

The one convention is **`TREAT_ABOVE = 50`**: half the Resolve Check, and the
cheapest slot removes 45, so below it a treatment throws away most of what it
buys. Falsifier in the comment — if treated heroes routinely hit 0 with heal
left over, it is too low.

## The map panel reads the whole-map view at last (2026-09-05)

Minimum zoom is the only view showing the entire dungeon, so it is where the
router should stand -- and it was the one view `find_boxes` could not segment.
`captures/04_minzoom_map_panel.png` against its own save fitted NOTHING; it
now fits at 12.4 px/unit with 16 of 19 cells explained and all seven rooms
resolving to seven distinct points. Live on a Warrens map the same day: 13 of
14 areas resolved, and the fit was byte-identical across eight consecutive
grabs of a settled view.

**The cause was the segmentation, not the fit.** Row runs were taken over a
whole column span, so cells at different heights in different columns joined
into one y-run and the panel came back as four full-height columns -- 36x75,
11x75, 12x75 and 50x75, where a room there is 34 px and a hall is 12.
Alternating the x and y tightening until neither moves splits them, and it can
only ever refine what the three fixed passes returned, so the tutorial corpus
is unmoved at 6 boxes and 48.0 px/unit.

One merge survives and is deliberately left alone: a room and a hall touching
only at a corner come back as their bounding box whichever axis is cut. It
measures 5.1 units against a room's 2.7, so the size rule calls it UNKNOWN and
it costs a vote rather than casting a wrong one.

`bridge/mapicons.py` is the second constraint this layer never had. The panel
draws a room at 64x64 and a hall tile at 24x24 and scales both by one zoom
factor, so a cell's SIZE says which it is -- and `Area.is_room` says the same
thing from the save, independently of anything measured off the picture.

**Both predicted sizes come off the sprite sheet with nothing fitted.** A hall
sprite IS the tile pitch, so a room is `scale * 64/24` and a hall is
`scale * 21/24`, the 21 being the hall sprites' measured ink extent inside
their own 24 px. Against four banked frames at scales 48.0, 15.3, 12.4 and 7.7
the rooms measure 2.688 / 2.680 / 2.742 / 2.727 and the halls 0.812 / 0.850 /
0.968.

* **Scored per BOX, never per tile.** A room's box is ~2.7 units across, so
  the corridor tiles beside it fall inside it; asking "what is drawn where
  this tile is" reports a room for several hall tiles and the correct fit
  scores 0.46. Asking "what is this box, and which tile is nearest its centre"
  scores 1.00 on the same frame.
* **The absolute size test, not the relative one.** `classify_boxes` takes the
  smallest cluster on screen as the hall unit, and an unscouted generated
  dungeon draws rooms and no corridor at all -- so it called all eight rooms of
  `03_ruins_map_panel.png` halls and refused a correct fit.
* **Opt-in.** `fit(boxes, tiles)` with no `tile_kinds` is exactly what it was.

**IT IS NOT ENOUGH ON ITS OWN, and the live run said so.** A fit can score
`explained` 0.85 AND kind agreement 1.00 and still be a whole cell out -- the
map has repeating structure, so a shifted transform lands rooms on rooms. What
caught it live was the tooltip.

### Two live lessons about the tooltip gate

**Park the cursor before the frame you FIT, not just before the frame you
compare.** The tooltip is drawn over the panel, `find_boxes` sees the plate as
a large bright box, and `locate` then returns the PLATE's centre: measured at
(840, 584) against a true (912, 620), with the tooltip itself banked at
(835, 587). `_map_offers_move` parks first and says why; an ad-hoc probe that
skipped it produced a confident wrong point in one grab.

**And the gate discriminates perfectly when it is asked properly.** Hovering
each candidate on a settled frame: 1590 and 1577 brighter pixels on the one
legal destination, and **exactly 0** on four other drawn rooms, a hall tile
and empty panel ground. The only room the game offered was `rooA` -- which is
independently what `route.plan_route` had chosen from the save. Two systems
with nothing in common agreeing on one cell is the strongest check this layer
has ever had.

**A map click from a HALLWAY does nothing.** The tooltip appears, the click
lands, and the party does not move; `W` at the same position moved them into
`rooA` in two seconds. The map is how a ROOM is left -- which is what
`plan_room` has always said -- and the tooltip is a necessary condition for a
legal destination, not a sufficient one for a legal click FROM WHERE YOU ARE.

`_nearby_cells` now offers room-sized near misses before corridor ones: only a
room is a destination, each candidate costs a hover of about a second against
a budget of four, and an UNKNOWN cell outranks a confident hall because a
merge often holds the room.

## A MOVE IS INVISIBLE TO THE SAVE (2026-09-06)

`persist.raid.json` keeps the party's MARCHING order and never records an
in-combat swap. That fact already had a whole module built on it --
`bridge/actionbar.py` exists because of it -- and the actuator did not know:
`_send_and_confirm` waits for a save write, so **a move that worked perfectly
was reported as "no save change in 15s - the input did not land"**, the turn
was given up, and the real formation drifted away from the save. Every target
computed afterwards was then off, which is the stale-formation symptom the
rest of this file keeps chasing.

Seen live: a Highwayman the save put at rank 4, MOVE lit, `MOVE to rank 2`
sent, nothing acknowledged. Everything about the send was right --
`combat_move_skill: .move 2 2` makes a 2-forward legal and the engine's own
bound agrees, and the click landed on the sprite (client x 416 against a
measured ~405; the LOG prints desktop coordinates, which is worth knowing
before comparing them to a layout constant).

The bar is the witness, for the same reason it witnesses a stance change: a
hero at a different rank can launch different skills, so the lit/greyed
pattern moves. A MOVE still **ends** the turn -- unlike a free action -- so
the key is marked either way, and a bar that did not change is reported
honestly rather than retried: the alternative is asking the same hero forever,
and the only other witness could never have answered.

**Three exceptions to save-is-truth now, and they are one shape.** The save
records the state BETWEEN turns:

| what happens inside a turn | the witness |
|---|---|
| whose turn it is | `bridge/heropanel.py` -- the HUD portrait |
| which rank a hero stands in | `bridge/actionbar.py` -- the lit/greyed signature |
| a free action, and a swap | the same bar, before and after |

## THE PANEL SAYS WHOSE TURN IT IS (`bridge/heropanel.py`, 2026-09-06)

The save is wrong about it on about 2% of raid turns, that is recorded
elsewhere in this file, and the recovery has been a GUESS -- `next_hero(skip)`
plus a bounded retry. The screen has been saying the answer the whole time.

The frame that named it: the log read *"[R2] Antiquarian (rank 3)"* and the
combat panel read **"Verrall / Duelist"**, with the Duelist's slot 3 drawn
SELECTED -- gold border, diamond marker -- because the keystroke had landed on
her bar. Six misses in one raid at 15 s each.

**And the timeout was never the whole cost.** The digit selects a skill on the
other hero's bar and DD dims the board into target-select, so the game is left
holding a live selection that a later click anywhere legal would execute.
Refusing before the send is both faster and the only version that leaves the
game untouched.

DD draws the acting hero's portrait at the left of the HUD and it is the same
`*_portrait_roster.png` art the stage coach and the roster list use, so the
machinery already existed one module away. Measured on four banked frames:

| frame | reads | cost | gap |
|---|---|---|---|
| the Duelist raid, twice | `duelist` | 6.28 | **12.46** |
| `battle_bar_shieldbreaker_rank1` | `shieldbreaker` | 22.77 | **11.38** |
| `battle_bar_plague_doctor_rank1` | `plague_doctor` | 25.14 | **18.22** |

Four for four, and the last two are 683x384 and matched against **all twenty
classes** rather than a party of four -- ground truth nobody chose for the
test, since the fixtures are named after their own hero.

- **The GAP is the statistic, not the cost.** A low-resolution frame costs 25
  where a 720p one costs 6, so an absolute cut set for the worse case admits
  anything at the better one. The gap holds above 11 in both, and a crop slid
  a third of a box off the art scores about 5. `MATCH_MARGIN = 8.0` sits in a
  space where the two populations do not overlap.
- **The box is a fraction of the CLIENT rect.** A fixed pixel box passes the
  720p frame and silently fails everywhere else, which is what the
  second test exists to catch.
- **One-sided, like `actionbar.read` and `_panel_is_open`.** It names a hero
  or it refuses, and a refusal leaves the save holding its answer -- right 98%
  of the time, so refusing costs nothing that was not already being paid,
  while a wrong name sends a keystroke to the wrong bar. A pool of ONE is a
  refusal too: one candidate cannot be confirmed by a comparison.
- **The key is marked spent only when `on_turn_missed` is wired**, the same
  rule the timeout path follows: with a recovery the mark is a handover, and
  without one it is the whole loss.

**A dimmed, desaturated battlefield is NOT this** -- it is torchlight zero,
and Fran said so within a minute of seeing the frame. It cost an hour of
looking for a blocking animation. The HUD stays in full colour when the light
goes out, which is exactly why `actionbar` reads the bar correctly on such a
frame and why the whole-frame saturation (0.14 against 0.25) is a torch
reading rather than an input-state one.

## A FREE ACTION IS NOT A TURN, and the save cannot see it (2026-09-06)

Fran, watching a live raid: *"the duelist change forms but didn't do the second
action ... the model did the second action and didn't expect it as it expects 1
action per hero"*.

`.is_continue_turn` -- the hero acts and **keeps** the turn. A stance change is
the Duelist's and the Sibyl's whole class identity, and the Abomination's
transform is the vanilla instance of it. `engine/combat_engine.py` has honoured
it since the Abomination existed. `bridge/` had never heard of it, so the
actuator sent the stance change, put the turn in `acted`, and waited for a turn
the game had already handed back.

**And the SAVE CANNOT WITNESS IT.** `persist.raid.json` is written per TURN,
and a free action does not end one -- so `_send_and_confirm` reported

```
[act] no save change in 15s - the input did not land (attempt 1/1)
[act] giving up on this turn - play it yourself.
```

about an input Fran could see landing on his screen. **The action bar is the
witness**: a new stance lights DIFFERENT skills, so a changed skill pattern is
the game acknowledging the change itself.

This is the third documented exception to save-is-truth, after "the save is
wrong about whose turn it is" and "the screen is truth about the formation",
and all three are one shape: **the save records the state BETWEEN turns**, so
anything that happens inside a turn needs a different witness.

- **Read `.is_continue_turn` off the store's skill index, never a class list.**
  `run.py::_continues_turn`. Three vanilla classes have it and a modded stance
  class would too, with no entry anywhere -- the same reason
  `UNRESTRICTED_HEROES` is derived rather than listed. Unresolvable is
  `False`: an unknown skill ends the turn, and being wrong that way costs one
  poll rather than a double-press.
- **The turn is not marked played**, so the next poll plans the same hero --
  and gets a real action, because the stance skill is `.per_turn_limit 1`, the
  game greys it out, and `plan_one_move`'s bar guard reads that.
- **`_report_played` fires only if the bar agreed.** It feeds the per-battle
  use tracker whose own rule is "only after the save confirmed it, because a
  use that never landed would suppress a legal skill for the rest of the
  fight". The bar stands in for the save here; if the bar could not be read
  either, nothing is reported and that rule survives intact.
- **`MAX_FREE_ACTIONS = 3`, bounded by the DATA.** Every shipped stance skill
  is `.per_turn_limit 1`, so two on one turn is the game disagreeing with
  itself and three is a loop. A hero asked forever is the failure this file
  keeps rediscovering.

### The bar is read TWICE, because the save is ahead of the screen

`run.py` polls the save, so a turn is planned the instant the save says a new
hero is up -- which is exactly when the screen is still finishing the previous
one's animation and still drawing the PREVIOUS hero's bar. One grab there
reports that hero's lit slots.

Measured across two consecutive iterations, on provisional turns (the save
saying enemy turns come first):

| | lit bar | `......` |
|---|---|---|
| before the settle test | 3 of 4 | 1 |
| after it | **0 of 4** | 4 |

**Those three lit readings nearly bought a wrong fix.** They look exactly like
"the save is wrong about the timing and the game is really asking now", and
the next change was going to be "let the bar overrule the timing gates". It
was the bar being read mid-animation. Two consecutive reads agreeing on the
four skill slots is the settle test; disagreement to the end of a 0.6 s budget
returns `None`, which is what "I am watching something change" honestly is.

### A dark bar is a TIMING fact, and deferring it is not optional

`bar.waiting` false -- neither MOVE nor the X lit -- means nobody is being
asked. That used to fall through to `plan_one_move`, which refused each
candidate with *"slot N is greyed out - it is not legal from the rank this
hero is really standing in"*: true of the pixels and wrong about the cause,
and it sends a reader after a formation bug that is not there.

It is refused once now, beside the three timing gates -- but **it may not be
marked spent the way they are.** Those are safe because `turn_key` carries
`enemies_first`, so the moment those turns resolve the key is a different one.
A dark bar changes nothing about the key: same hero, same round, same turn
count the instant the animation ends. Marking it drops a live turn.
`MAX_DEFERRALS = 12` looks again, then hands it to `run.py`'s stall heartbeat,
which is the layer that knows how to try somebody else.

## THE HOVER GATE PULSES, AND ONE LOOK IS A COIN FLIP (2026-09-05)

The section above calls the tooltip gate "the strongest check this layer has
ever had", and it is — **when it is sampled properly**, which it was not. The
party stood in `rooJ` for two whole 900 s iterations and Fran, watching the
screen, described the failure exactly: *"when it sets the mouse on it and then
tooltip shows it's not clicking. never leaving the room"*. The tooltip WAS
appearing. `_map_offers_move` was looking once, at a fixed 0.5 s offset.

Measured live, sampling every 0.2 s on a cell the game does offer:

```
+0.23 none   +0.93 none   +1.64 none   +2.34 none
+0.47 none   +1.16 HIT    +1.87 none   +2.57 HIT
+0.70 none   +1.40 none   +2.10 none
```

**About one hit every 1.4 s**, so a single grab found it roughly one time in
six and the route layer read a granted permission as a refusal. Sampled over a
window instead, the separation is wide: **9 to 12 hits on the offered cell
against 0 to 2 on every other**, in the same pass, three passes running.
`TOOLTIP_SAMPLE_FOR = 2.0`, `TOOLTIP_SAMPLE_STEP = 0.2`, `TOOLTIP_MIN_HITS = 3`
— three times the noise and a third of the signal — with an early exit on the
third hit, so an offered cell answers in under a second and only a refusal pays
the whole window.

**Run the parked control before trusting a longer window.** That is what makes
this more selective rather than merely more sensitive: cursor parked, nothing
hovered at all, **twelve consecutive samples over three seconds fire ZERO
times**. The panel does animate — the party's torch pulses, a curio icon
glints — and `screen.what_appeared` is already blind to it, because it asks
what APPEARED rather than what changed. Anything that adds sampling time to a
one-sided pixel test owes the same control.

### When the fit cannot name a cell, ask the GAME — ordered by the SAVE

The other half of the same stall: `_map_offers_move` needs a cell to hover, and
on that map there was none to give it. `mappanel.fit` returns **None outright**
on the 33-cell whole-map view, and the settled 9-cell view fits at **38.1
px/unit against a true ≈11.4**, predicting every room hundreds of pixels off
the panel. `find_boxes` there returns twelve three-pixel slivers.

So the fallback drops the transform entirely and orders the candidates out of
two things that are not measurements of the panel's geometry:

- **The party's own cell, found by the warmth of its torch.** Fraction of
  pixels with `red > green + 25 and green >= blue and red > 90`: **0.155** on
  the party's cell against **0.014** for the next-warmest and **0.000** for the
  other twelve. An order of magnitude, which is why `PARTY_CELL_WARM = 0.06` is
  not a tuned number. No marker, no origin, and `_party_cell` returns **None**
  rather than guessing — a direction from the wrong origin points somewhere
  confidently wrong.
- **The direction to the destination in map units**, `_map_offset` off
  `Tile.mappos`, straight from `persist.map.json`.

Room-sized cells are then ranked by cosine against that direction, anything
under 0.3 dropped as pointing the wrong way, and up to `BLIND_CANDIDATES = 5`
hovered. On the banked frame that ranks the room below the party first at
**cos 1.0** — which is the answer Fran gave before the code found it: *"it
needs to click on the room below"*.

**And when the game confirms none of them, take the best-ranked cell anyway.**
"Not clicking blind" is the right rule when blind means *unordered*, and it was
— the only ranking was a fit, and a wrong fit put a room 72 px from where a
right one put it. It is the wrong rule now: this ordering comes from the save.
The two outcomes are also not comparable in cost. A click that misses is **one
poll** — the save does not move, the next poll re-measures, and
`_send_and_confirm` is already capped at one attempt for a map point precisely
because a coordinate does not survive its own miss. Never clicking is the raid.

`captures/map_whole_view_rooJ.png` and its `.map.json` are banked so all of it
is pinned offline.

## The first raid driven end to end, and the four things it found (2026-09-05)

`profile_8`, three heroes, the Warrens. Every fix below is something a layer
already KNEW and had no way to act on, and three of the four were invisible
until the whole loop ran against a live game. The raid ended in a wipe, which
is the point: it is the first time the bridge got far enough to lose one.

### RETREAT had no button, so the policy's own top pick was unplayable

Both rulers called the fight off. The checkpoint ranked `__retreat__` at 72 to
78% for three turns running and `advise_retreat` priced staying as a certain
death -- *"the run loses somebody 100% of the time, over the 50% floor, so the
gold does not get a vote"*. `advised_button` returned **-1**, `plan_battle`
fell down the shortlist to an attack each time, and two heroes died.

**This is the mechanism behind Fran's "several deaths which I didn't really
understand why it went for that kind of play".** The 2026-09-03 work found the
retreat PRICING was broken and fixed it; the actuator could still not send
what the pricing asked for, and nothing said so out loud -- the fallback note
mentioned it in passing, inside a line about attacking.

**The control is the raid HUD's X**, the same icon that abandons a quest out
of combat, and the game names it rather than us inferring it:

| string | text |
|---|---|
| `[pc]str_help_raid_combat_4` | "[CLICK] on the retreat icon (looks like an "X") to attempt to retreat from the combat" |
| `[pc]str_help_raid_room_5` | ...same icon, "to abandon the quest" |
| `[pc]str_help_raid_hallway_7` | ...same again |

`layout.RAID_ABANDON_QUEST` has been measured since 2026-09-01 and the retreat
path was never pointed at it. `RETREAT_SLOT = 7` in `overlay_slots.py` is what
lets a caller tell "the advice is retreat" from "the advice has no control",
which used to be one answer.

**One control, two meanings, and that is why `plan_retreat` is reachable only
through `plan_battle`.** Out of combat the same click throws the expedition
away. `test_only_a_BATTLE_can_reach_the_retreat_plan` asserts the call graph
structurally rather than trusting a flag somebody has to remember to pass.

The X only ASKS -- DD draws "Are you sure you want to retreat?" with Yes on
`MODAL_OK` -- so the plan is two clicks and they are **one plan**, because an
X with no confirm leaves a dialog over the board swallowing every later input.

### A stalled walk gave up one line above the `W` that would have moved it

The party stood at the far end of `coAD` with `rooA` through the door in front
of it. `D` moved nothing three times, the walk printed "something is in the
way" and returned -- while the door press twenty lines below, the only thing
that opens a door, was reachable **only** by exhausting all twelve
`MAX_WALK_STEPS`. Pressing `W` by hand at that exact position put the party in
`rooA` in two seconds.

It now `break`s instead of returning. Same rule as `_LayerDone` one layer up: a
step that cannot act must not block the one that can.

**`W` is right for the other stall too.** It is the game's universal "interact
with what is in front of you" (`str_help_raid_hallway_4`), so it clears an
obstacle as well as it opens a door. And the case this must never become --
`W` walking the party BACK out of the entrance, which is why an unconditional
`W` was reverted on 2026-09-01 -- cannot arise here: the direction is derived
from which end the party came in by (`Area.standing_on`, graded 224 of 224
against `raid.areatile`), so a stall is always against the far end of the
walk, never against the door behind it. **That is the `Heading` question the
old note said was unanswered; the save had already answered it.**

### The pack described free slots and took none

`spare_stack_slots` has printed *"2 stacks of torch hold 3 of a 8 limit -
merging them frees 1 slot(s), for free"* since the pack layer existed, and the
very next line of the live log was *"nothing in the pack is free to discard"*,
followed by a pause that ended the session's automation.

`inventory.plan_stack_merges` executes it, and `ActRunner._merge_stacks` drags
-- a drag and not a click, because SDL decides a drag happened from the motion
events BETWEEN press and release, the same reason the coach's hire is one.

Two rules:

* **Only a pour that EMPTIES its source is emitted.** The acknowledgement is
  the pack getting SHORTER, so a partial merge -- topping the destination up
  and leaving the source holding the remainder -- comes back the same length
  and is indistinguishable from an input that never landed. `src + dst <=
  limit` makes every drag verifiable by one number.
* **Merging is tried before a discard is ever considered.** It throws nothing
  away and needs no judgement about what is chaff. Of 399 captured packs, 34
  have a merge available; a test requires `spare_stack_slots` and
  `plan_stack_merges` to agree on every one of them, because the failure being
  fixed IS the two disagreeing.

### The only campaign the bridge owns could not be loaded

`scroll_plan` and `MENU_SAVE_SCROLL_DOWN` were both written, tested and never
reached from `plan_for`, so every campaign below the second visible row was
unreachable -- and on this machine that is `profile_8`, the bridge's own. The
`--slots` CLI reported it "UNREACHABLE" and named `-profile N` as the way out;
**`-skipfeflow -profile 8` hangs the game** even though slot 8 exists (dead in
`app.log` 43 lines in, before any `as_*` state, exactly as the 2026-08-17 probe
recorded for a slot that did not).

It now scrolls, **one click per plan** so the kill switch can stop it halfway
and so the rows can be re-read between presses. Driven live: four arrow
presses, one row click, into the raid.

**The row click is irreversible in both directions, so it is gated on the
row's CONTENT as well as its index.** The disk knows whether slot N holds a
campaign and the frame knows whether the row draws one:

* expecting a campaign, finding an empty letter -> the scroll landed somewhere
  else, and clicking would **START** a campaign nobody asked for (which has
  happened here once, on a wrong belief about how the list is indexed);
* expecting a letter, finding a campaign -> clicking would **LOAD** somebody
  else's.

A gate that cannot see does not also decide: an unreadable frame returns "go
ahead", because `plan_for` is what refuses an unreachable slot.

**The delete-column guard is now the column, not a flat `x < 1800`.** The
scrollbar sits legitimately to the right of the X -- 2156 against 1935, 221 px
clear -- so the flat bound would have had to be loosened to scroll at all.
Naming the hazard is stricter than moving the fence: the old rule permitted
every x below 1800 and so does this one, and this one also refuses the band
that actually deletes something.

## The panel has an ITEM SLOT, and the obstacle that ended a raid (2026-09-05)

The fifth thing the first end-to-end raid found, and the one that actually
stopped it. The party reached `coNO` in the Cove with **four shovels in the
pack**, the corridor's tile 2 was an obstacle, and:

```
[act] [route] toward rooN (LEAVING barnacle_crusted_chest BEHIND: curio
      barnacle_crusted_chest wants shovel used on it, and only the
      bare-handed W is bound)
[act] [route] 3 steps toward rooN moved nothing - trying the door
[act] [route] at the end of coNO - W into rooN
[act] [route] still in coNO after 12 steps each way - stopping
```

Every one of those lines is correct about what it did. The `W` **opened the
Shipwreck panel** -- and nothing in the bridge could click anything in it, so
the panel stood there modal over the world and the walk ran its budget out
against a corridor it could not enter. The raid ended in a hallway.

### The row is a grid, and the pitch is measured twice

`bridge/curiopanel.py` found the red X and derived the hand from a single
recorded offset. The X is still the anchor and for the same reason -- the panel
flows around a description of whatever length, so its y is not stable and its
right edge is. What is new is that the whole row follows from it:

| frame | X | item | hand | pitch |
|---|---|---|---|---|
| Shipwreck, 1366x768 | 1040 | 956 | 882 | 79.0 |
| Traveler's Tent, 1280x720 | 974 | -- | 826 | 74.0 |

`79.0 / 1366 = 0.05783` against `74.0 / 1280 = 0.05781`. That is
`SLOT_PITCH_FRAC`, and the historical `HAND_FROM_X` is exactly two of it --
which is why the old constant clicked correctly for weeks. A test pins that the
two still agree inside one button.

**THE ITEM SLOT IS ALWAYS DRAWN.** `travellers_tent` has **zero** item
interactions in the curio library and its panel reserves a slot anyway, drawn
dim. So the row is three wide whatever the curio is, and the hand is at a fixed
two pitches -- which is what makes the item slot a *position* rather than a
search.

### The hand is CHECKED by its gold plate, and that is the panel-open test

A dim slot and an absent one are indistinguishable (p95 luma 57.0 against
58.0), so counting slots cannot say which one is the hand. Colour can: the hand
sits on a warm gold plate and nothing else in the row does.

| slot | warm-and-lit fraction | Shipwreck | Tent |
|---|---|---|---|
| -1 item | | 0.004 | 0.000 |
| **-2 hand** | | **0.115** | **0.114** |
| -3 empty | | 0.000 | 0.004 |

**And it is the panel-open test the module never had.** Over the 302 frames
this repo holds, the red-X rule alone fires on **62** -- the pause menu, the
raid-results page, the estate map, two dozen Hamlet screens. Requiring the
plate at slot -2 leaves **exactly the two real panels**.

It has to be a BAND and not a floor, which is the part worth remembering: the
nearest contaminant from below reads 0.067 and the nearest from above 0.130, so
a floor at 0.05 admits 24 town screens and a floor at 0.09 admits the other 15.
A band is defensible here only because a fixed sprite has a fixed fill.

`CurioButtons.hand` is `Optional` now. A row this module has not seen reports
`hand=None` and **still reports the X**, because closing something you cannot
read is the safe move and a panel left standing swallows every later keystroke.
`_dismiss_stray_curio_panel` requires `readable`, which is strictly stricter
than what it did before.

### Two lit slots are REFUSED, not picked between

`bridge/curios.py` names one provision out of what the party carries. Eight of
sixty curios accept two, and with two lit slots nothing on screen says which is
which -- so `sole_item()` returns None and `_use_on_curio` clicks the **X**.

**It never falls back to the hand.** The bare-handed outcome of a curio that
wants holy water is often the bad one, which is precisely why `curios.py` named
an item. That rule used to be enforced by refusing to press `W` at all; the
refusal moved down a layer, to the panel, where the evidence is.

`plan_room` therefore makes a `use` curio actionable: the plan is the same `W`,
and which button gets clicked is decided against the frame.

### An obstacle is not a curio — `bridge/obstacles.py`

Three differences, all load-bearing:

* **The save does not name it.** A curio tile carries `curio_id`; an obstacle
  tile carries `content == 4` and nothing else. Checked against the live Cove
  map: `coCH` tile 1 and `coNO` tile 2 are both `curio=None trap=None`.
* **Walking away is not an option.** It is standing in the corridor the party
  has to walk down, so there is no `skip` -- only which of two ways to clear.
* **Bare hands always work.** `props/prop_definitions.json` ships
  `clear_on_failure: true`, at `health -0.05`, `torchlight -20.0` and
  `fail_effects ["Stress 2"]`.

So which tool comes from the ZONE, derived rather than remembered. The base
`obstacle` prop declares `interaction_type: "shovel_obstacle"` and every
obstacle inherits it; `<zone>.props.darkest` says which props a zone can draw:

```
cove       obstacles: .chance 1 .types shipwreck
crypts     obstacles: .chance 1 .types rubble
warrens    obstacles: .chance 1 .types rubble
weald      obstacles: .chance 1 .types thorny_thicket
```

One type each, all inheriting the base, so all four base zones want a shovel --
and the word does not appear as an answer anywhere in the module, which a test
enforces by counting it.

**The Courtyard is why it refuses instead of defaulting.** It draws `mist` (a
torch obstacle) and `pillar` (a shovel one), so the zone alone cannot name the
tool and `tool_for_zone` returns None. The caller then clears it bare-handed,
which is correct rather than merely safe: guessing would spend the wrong
provision *and* leave the obstacle standing.

`zone_from_save` reads `raiddungeon` out of **`persist.game.json`**, not the
raid file every other reader here takes. And `tools/dson.parse_file` hands back
a `Field` tree, so `.get("raiddungeon")` is the child FIELD and the string is
on its `.value` -- reading the field itself is how this first returned `""`
against a save that plainly said `cove`.

### The hook: a `W` that opens a panel did not open a door

`_work_obstacle_panel` runs immediately after the route layer's door `W`, ahead
of the door watch, because a panel is modal and the party will never leave the
corridor while it stands. Positive evidence only -- it grabs one frame and
returns False unless a **readable** panel is on it, so a `W` that really did
open a door costs a screenshot and nothing else.

### Flown live, 2026-09-05

Against the raid that had been stuck for a day:

```
panel : hand (0.6457, 0.4280)  items (0.7035*)  X (0.7613, 0.4280)
zone  : cove
[act] [route] coNO is blocked and the panel is up - clear the obstacle with
      a shovel - cove obstacles want a shovel and the pack has 4
worked: True
panel after: none
pack  : shovel 4 -> 3
coNO tile 2: obstacle -> empty
```

Both halves acknowledged by the save, and the party paid a shovel instead of
5% max HP and 20 torch. `coCH` tile 1 is still an obstacle, in a corridor they
have not reached.


## The save and the screen disagree about whose turn it is, 2% of the time (2026-09-05)

Found by driving the raid the obstacle fix had just unblocked. The party
reached a fight, the Runaway played twice, and then:

```
[13:25:45] [R2] Runaway (rank 2, 13/23 hp, 20 stress)
  -> SEARING STRIKE on rank 3 (Deep Stinger, 2 hp)   85%
  [act] [battle] Runaway: SEARING STRIKE on rank 3
    KEY  1      select slot 1
    CLICK  1007,365   enemy rank 3
  [act] no save change in 15s - the input did not land (attempt 1/3)
  [act] no save change in 15s - the input did not land (attempt 2/3)
  [act] no save change in 15s - the input did not land (attempt 3/3)
  [act] giving up on this turn - play it yourself.
```

The banked frame shows **Reynauld the Crusader** on the action bar, his skill
row lit and his stats drawn. The save, written at the same second and not
touched since, says the Runaway:

```
[0] monster 99   ini  10.007  turns 2
[1] monster 100  ini   9.007  turns 2
[2] hero     61  ini   6.005  turns 1     <- current_actor
[3] hero      1  ini   6.001  turns 0     <- the screen
[4] monster 98   ini  -2.005  turns 1
[5] monster 97   ini  -3.005  turns 1
```

The failure is silent and total. `advised_button` maps the advised skill to a
slot number through **the advised hero's** `selected_skills`; the number key
selects that slot on **whoever is actually acting**; the click then aims at a
rank that skill may not reach. Nothing lands, nothing errors, and after three
retries the run stops for good.

### No counter-based rule does better — measured, not argued

Every consecutive pair of captured snapshots in the same fight where exactly
one combatant's `performing_turn` advanced is a graded decision: the game asked
for whoever's counter moved. 399 snapshots give 153 such pairs, 110 of them
hero turns — the only ones the bridge acts on.

| rule | all 153 | the 110 HERO turns |
|---|---|---|
| **A** — shipped: first pending, prefix-scanned on `performing_turn` | **145 / 8** | **108 / 2** |
| **B** — `currentturn + 1` | 101 / 52 | 108 / 2 |
| **D** — fewest turns taken first | — | 108 / 2 |

**A is the best available and the alternatives are not better; on hero turns
all three miss the same two.** So the shipped rule stays exactly as it is —
and `currentturn` stays rejected, which the corpus reaffirms twice over (it is
right on only two thirds of the full set).

Two traps in building that measurement, both worth keeping:

* **`battle_seed` is the RNG state, not a fight id.** It changes on every
  write, so pairing on it matched nothing at all — the first run of the harness
  reported "0 gradeable". A fight is its battle guids plus
  `battles_completed`.
* **Grade the decision the bridge MAKES.** Over all 153 pairs A beats B by 44
  points; restricted to hero turns they tie. Most of A's margin is on monster
  turns, which the bridge never acts on, so quoting the headline would have
  claimed a robustness the change does not have.

### So the 2% is recovered from, not predicted away

`LiveState.next_hero(skip)` walks past heroes whose turn would not land, and
`other_heroes(guid)` reaches one the prefix scan has excluded — which is what
**both** corpus misses are. `Advisor.recommend(state, skip)` threads it
through. `run.py` owns the counter:

* `ActRunner.on_turn_missed` fires when a battle turn will not land;
* `Session.note_turn_missed` raises `_battle_skip` and clears the dedupe key;
* `_battle_skip` is **part of the battle key**, or the retry re-derives the
  identical tuple and the dedupe drops it;
* `_turn_signature` — every counter, not just the round — retires the skip the
  moment the board moves, because the skip is a hypothesis about a stuck
  position and the evidence that ends it is the position becoming unstuck;
* `MAX_TURN_SKIPS` bounds it, since every extra try is a real input.

**The key is marked spent ONLY when a recovery is wired.** With `run.py` behind
it, the mark is what stops the dedupe re-serving the dead move; with a bare
runner or `--replay` nothing else would ever try, so the key stays open exactly
as before and the turn can still be played by hand.

### A send can be FALSELY CONFIRMED, so the failed-send path is not enough

`_send_and_confirm` calls a turn landed when the save changes at all — and an
enemy acting is a change. So the wrong-hero case has a second shape with no
failure in it anywhere: the input goes nowhere, an enemy moves, the save is
rewritten, the send reads as confirmed, no counter advances, the dedupe key is
identical and `_watch` skips every later poll because the signature has not
moved. **The bridge goes quiet with the game waiting.** Seen live immediately
after the first fix: three real turns played, then silence, the Runaway on
Death's Door and the Crusader's bar up.

`STUCK_SECONDS` (25 s) is the heartbeat that catches it — a battle save that
has not been written for that long is a battle nobody is playing, so the
watcher asks `note_turn_missed` for another hero. Comfortably longer than a
hero turn resolving (measured 0.3–2.5 s) and than a run of enemy turns
(measured at 17 s of silence between two hero turns), and bounded by
`MAX_TURN_SKIPS` so it asks once per hero rather than pulsing.

`stuck_since` and `in_battle` are remembered across polls precisely because
this check runs on a poll that parses nothing — it cannot ask the state whether
a fight is on.

**Both halves fired live, in one fight.** The failed-send recovery three times,
the heartbeat once (`-- nothing has moved for 25s and a fight is on; trying
another hero --`), and between them the bridge played a Cove fight from round 3
to round 7 with seven confirmed sends. It lost — the Runaway bled out and the
Crusader ended on Death's Door — but it PLAYED it, where an hour earlier the
same fight had been frozen on one turn.

### And the retry budget had to come down with it

The disagreement hit roughly every other turn, and each one cost 45 s: three
attempts at 15 s, re-sending the identical plan. That budget is right for what
it was written against — focus lost, a backend hiccup, an animation still
running — and useless against a wrong hero, where the same keystroke selects
the same wrong slot every time. So **one attempt when `on_turn_missed` is
wired, three when it is not**: unwired there is nothing else to try.

### A callback nobody assigns is a feature that does not exist

Every piece of the above worked, the suite was green, and the first live re-run
behaved **exactly as before** — because `main()` never set
`runner.on_turn_missed`. One line, below anything a test can construct.
`tests/bridge/test_run.py::test_the_recovery_is_actually_WIRED_UP` walks `run.py`'s
AST for the assignment, the same reflex as `tests/training/test_train_wiring.py` asking
the workers what they received.


## THE ROUTE IS DECIDED ON LOOT NOW (`bridge/lootplan.py`, 2026-09-06)

Fran: *"minimizing low torch battles + maximizing loot at the same time"*, and
*"improve the layout planner in dungeons to really optimize loot in the end
using all the available info to us, even info we only get via code rather than
seeing it ingame"*.

`bridge/route.py` said in its own docstring that it could not do this --
*"optional detours are priced, not decided ... how a player trades moves and
fights against loot is a judgement no shipped file contains"*. That was true
when it was written. It is not true now, because three shipped files nobody had
read turn out to contain exactly that judgement.

### The three files, and what each one settles

| file | what it says | who read it before |
|---|---|---|
| `loot/darkness_overrides.loot.json` | per event kind, the CHANCE of an extra draw and which table it draws | nobody |
| `loot/*.json` (58+ tables) | what a draw yields, recursively, weighted | nobody |
| `inventory/*.inventory.items.darkest` | `sell_gold_value` per item | nobody |

`rules.json`'s "+30% gold and +12 treasure draws" is a summary, and it hides
the mechanism. **"+12 treasure draws" is not twelve draws** -- it is a chance
of one extra draw from a named table, and at the bottom bracket the *chest*
chance climbs 75% -> 95% while the *battle* chance does not move at all: it
draws **twice**. Priced through the real tables at Weald/Apprentice:

| | torch 100 | torch 0 |
|---|---|---|
| a fight's bonus draw | 0g | **+454g** |
| a chest's bonus draw | 0g | **+165g + 0.39 crest** |
| gold multiplier on the base drop | x1.00 | **x1.30** |

### ...and the COST side, which was equally unread

* **`tile_light_loss`** -- 1 per tile above 75, **6 below**. Light is nearly
  self-sustaining while you hold it and collapses once you do not. Topping the
  torch up on arrival rather than at the fight took a real nine-room plan from
  four torches to **two**.
* **`corridor_return_content`** -- re-entering a cleared corridor
  **repopulates** it: battle 5% / hunger 7.5% / trap 5%, each **+5% at torch
  0**. A dark walk home does not merely risk what is on the map, it makes new
  fights. That is the price of a deferral and the exact quantity Fran's
  objective says to minimise.
* `hallway_stress` backing up is 5.0 at 55%, against 2.0 at 30% forward.

### The rule, which needs no invented weight

Fran chose a **hard torch floor, then maximise loot** -- no planned fight below
76 while the pack can afford the light, and among plans that respect it, the
most expected loot. Lexicographic, so nothing has to convert risk into gold.

The party has to walk back to the entrance to hand a completed quest in, so
those corridors are re-entered whether or not anything was deferred. Hence:

> **Defer what is on the way home. Take the rest on the way out.**

A curio on the homeward path costs **nothing** to defer and pays about three
times as much at torch 0. One that would need a detour is taken lit on the way
out instead, because deferring it would buy loot with the one thing the
objective is minimising.

### DEFER is a verdict, and it is not SKIP

* `skip` means never -- every outcome is net negative.
* `defer` means *not at this light*, and it carries the stop that comes back.

They reach `plan_room` differently on purpose. A deferral must NOT arm
`curio_action`, because the marker scores that against the save and would
record a curio deliberately left standing as advice the player ignored. It
must also not leave `RoomAdvice.empty` true, or the actuator prints "no room
advice to act on" and the party stands still -- the `_LayerDone` failure
again.

### The pack is ASSIGNED across the route, not chosen per curio

Fran: *"if we know the dungeon still have 3 extra chests, doesnt make sense to
drop the keys of the invetory for other things"*, and *"that chest should be
opened if we had a key"*.

`bridge/curios.py::advise` answers one curio at a time against the whole pack,
so with one key and two locked chests it says "use the key" twice. It is not
wrong to -- it is answering a different question -- but a router cannot use
that. `lootplan.assign_items` spreads the pack over every curio on the route at
once, exactly, by DP over the remaining counts, falling back to greedy and
saying so when too many item types make the state space large.

The Old Road is the worked example, and it is the whole argument in one map:
`bandits_trapped_chest` is **`Blight 1` bare-handed and loot table `T` with a
skeleton key**. Same map, same route, one item -- and the verdict flips from
SKIP to TAKE.

### Two declared readings, each with its falsifier

* **A loot-bearing curio draws on the `chest` bonus.** The file names only
  `battle` and `chest`, and a curio is neither word. Falsifier: open one crate
  at torch 100 and one at torch 0 and compare. If they match, only the gold
  multiplier applies -- which changes the SIZE of the deferral gain, not its
  sign, so the route survives.
* **`corridor_return_content` rolls per corridor re-entered, not per tile.**
  Per-tile would make a six-tile hallway a near-certain ambush, which is not
  how the game plays. Falsifier: walk one cleared six-tile corridor back at
  torch 0 ten times and count -- per-corridor predicts ~1, per-tile ~5.

### `CREST_GOLD` is the one invented number, and it is provably inert here

Heirlooms ship `sell_gold_value 0` -- the game says they do not convert to
gold. `data/loot_loader.py::CREST_GOLD` exists only so two routes offering
different currencies can be ordered, and `worth()` is the only thing allowed to
read it (asserted over the AST). `tests/bridge/test_lootplan.py` sweeps it across
three orders of magnitude and requires the Old Road plan not to move. **A map
whose plan DOES flip on it is a map the data cannot decide**, and has to be
reported as undecided rather than resolved by a constant.

```bash
python bridge/lootplan.py <persist.map.json> --have torch=4,skeleton_key=1
python -m data.loot_loader --darkness-table --difficulty 1 --dungeon weald
python -m data.loot_loader --walk-costs
python -m data.loot_loader --code A --difficulty 1 --dungeon weald
```

### The Old Road is a fixture now, and it never needs the game running

`scripts/starting_save/persist.map.json` is the tutorial map, shipped as
**plain JSON** rather than DSON, and `read_dungeon_map` reads it. So does
`maps/*.dm` -- the four Darkest Dungeon floors and the town invasion, which
are ordinary DSON. There is no capture of the tutorial anywhere in
`archive/traces/`, so before this the one map the bridge starts every campaign
on could only be tested live.

Two shallow differences from a live save, both handled in the reader: the map
hangs off `data.map` with `areas` directly under it, and a tile spells its
curio `cur` rather than `curio_prop`, writing **0xCCCCCCCC** where a live save
omits the key. That sentinel is MSVC's uninitialised fill, and without
`EMPTY_HASHES` every tile in the template reports a trap called
`#3435973836`.

## Marking: how a recommendation gets a verdict

`run.py` also **marks each recommendation against what was then played**, which no
capture could reconstruct before ("the advisor said rank 3" was testimony). It diffs
consecutive saves: a provision stack shrinking is an item, an enemy losing HP or
vanishing is an attack on that enemy, a hero gaining HP is a heal, stress coming off or
a buff appearing is support. That fixes the *kind and the target* — which is what
PLAYBOOK-level disagreements are about — and never the skill, since plain-damage skills
are indistinguishable in a diff. Two rules hold it honest and must not be regressed:

- **Several signals at once is usually one skill, not ambiguity**, so they are ranked
  (attack > heal > move > support) with the rest reported alongside. Duelist's Advance
  moves the performer *and* attacks; treating that as competing evidence marked 23 of 28
  unresolved turns on the first replay. The ranking is fixed and derived from the game's
  skill design — never "pick whichever signal the advice predicted", which would make
  the measurement agree with itself.
- **A miss leaves no trace, and so `?` is a real verdict**, printed as a rate in the
  summary rather than folded into agreement. 5 of 106 combat turns across the corpus
  (4.7%). Provisional advice (the look-ahead past pending enemy turns) is never marked.
- **The verdicts are also a checkpoint metric.** `Session(on_score=...)` reports every
  verdict with a key naming the decision point — round, whose turn, what was played, all
  from the save, so it does not move with the advice. `tools/agreement.py` uses it to
  replay the corpus per checkpoint and pair the results, which is the project's only
  measurement of a policy that has no simulator in it. It refuses to report a delta
  unless both arms scored an identical key sequence. See `CHANGELOG.md` for the finding:
  the two checkpoints disagree with real play mostly by **aiming** differently, not by
  choosing differently.
- **Both sides of a verdict are SETS, never one guid.** An AoE has no single target:
  `advised_target` expands through the engine's own `_resolve_action_targets` and
  `Observed.guids` carries everyone the diff saw change. Comparing one arbitrary
  member of the healed set against one nominal rank made the same Divine Comfort read
  MATCH or DIFFER by dictionary order. The residual permissiveness (a single-target
  heal inside a party-wide footprint reads MATCH) is **printed** as `(n of m in the
  advised footprint)`, not hidden — the board carries current HP, not max, so "who
  could have been healed" is not knowable here.
- **A guarded target's hits land on its guardian, and `judge` has to know.** `Board.
  guarded_by` is read off `stat_type` 27 (on the GUARDED combatant, `guarded_guid`
  naming the guardian). Guardians widen what counts as *reached*, never the
  *footprint* the `n of m` count reports, and the redirect is named in the verdict
  rather than passing silently — otherwise the rule degrades to "any enemy will do".
- **Corpses are in `Board.corpse_hp`, a dict of their own** — not in `enemy_hp` (a
  kill is a guid *leaving* `enemy_hp`; folding corpses in would turn every kill into
  "an enemy whose HP merely changed") and not absent (that made attacking a corpse
  invisible, so a followed recommendation was marked by whatever signal was left
  over). A vanished corpse counts as an attack **only within the same round**, since
  rot resolves at end of round and across that boundary destruction and decay are
  indistinguishable.
- **The five dungeon layers are marked too, and their rules are not combat's.**
  `Session.room` arms a `RoomAdvice` (structure, never the printed text — the
  overlay's rule, for the overlay's reason), and `replay()` dispatches to `room`
  as well as `battle`, which is what lets route/torch/curio/camp be measured
  offline against captures already on disk. Four rules keep the number honest,
  and each exists because the first version got it wrong: **the verdict is taken
  when the party leaves the area** (a room recommendation has no clock);
  **events are latched per poll, in battle as well as out**, since a torch relit
  and then walked with decays again and endpoints would read it as never relit;
  **a route step with one way forward is not scored** (`ways_forward` off
  `DungeonMap.graph()` — 36 of 40 captured steps are forced, and scoring them
  gave a meaningless 18-for-18); and **a curio walked past is parked, not
  failed** (`DEFER`, deliberately outside `VERDICTS`), because the party comes
  back for it — that alone was 4 of 5 curio DIFFERs. A snuff is discriminated
  from decay by crossing the whole darkest lit band in one step, and 25 is
  `rules.json`'s own boundary via `dark_ceiling()`, never written down.
  `same_raid()` drops a claim when the expedition ends, the room-side twin of
  the combat marker's round-going-backwards guard. Verdicts are **per layer and
  never pooled**; `Update.layer` keeps the overlay's badge about the combat turn.
- **The torch SHUTDOWN is asked from `Session.battle`, never from the room block.**
  `torchlight.advise` gates it on `about_to_win`, and the room block passes
  `state.in_battle` — which is False everywhere `Session.room` runs, so the play
  was structurally unreachable and had never been recommended once. `run.about_to_win`
  supplies the real answer (one living enemy, dying to `dmg_min` or already
  `_is_doomed` — the same secured-kill test `tools/check_playbook.py` uses).
  Do not "simplify" this back into the room block: out of combat there is no
  killing blow to precede and the layer will correctly refuse forever.
- **The dungeon block asks the torch layer `about_to_win=state.in_battle`.** It used
  to pass a hard `True`, so out of combat it printed the reverse of its own layer's
  advice — "SHUT DOWN before the killing blow" in a corridor where the correct answer
  was RELIGHT. Out of combat a `hold` prints nothing; the block leads with `NEXT ->`
  and what is in that area; the ahead-curio list is route-only and capped.
- **`Session.emit` must survive a cp1252 console.** Several layers print an em dash
  and `print` raises `UnicodeEncodeError` on those — from the daemon poll thread,
  which is the same silent death as a torn save. The log keeps the exact text.

`--replay <dir>` runs a whole capture directory through the loop offline, which is how
the marking is validated without anyone playing; `tests/bridge/test_run.py` pins that rate.

## The overlay

**`overlay.py` is the same loop drawn over the game** — the advice was always correct and
always in the wrong place, and what a terminal behind the window costs is not reading time
but *looking away*. It renders `run.py`'s `Update` records and decides nothing; it must
never parse the printed block back into structure, since a marker keyed on a regex over
`-> SKILL on rank 3 (...)` breaks silently, by pointing at the wrong rank. Constraints
that are not preferences:

- **It never clicks.** Layer 3 stays unbuilt for the same reason as always.
- **A look-ahead must never render as an instruction.** `recommend` reads past pending
  enemy turns so the screen is not blank through ~17 s of no save write (13 of 77 captured
  advisories are that). The `Update` carries `enemies_first` and `movers`, the headline
  leads with `2 ENEMY TURNS FIRST, then …`, the ring is greyed and dashed, and it is
  **withheld entirely** when a pending enemy can reposition. Dropping that lead is what
  produced the live report "the decision it recommends is not the one that's due" — the
  turn pointer was checked and cleared across all 97 usable captures.
- **The dungeon block is keyed on the darkness BRACKET, never the raw torch level**, which
  decays continuously while walking and reprinted all fifteen lines per point.
- **The square marks the button, and its number is the game's, not the action space's.**
  `Discrete(38)` indexes the hero's full 7-skill movepool; the bar shows only the four they
  brought, in save order, then MOVE (5) and the narrow X, which is PASS (6). So the slot is
  `LiveHero.selected_skills.index(skill_id) + 1` — going through the action index points at
  the wrong icon for any hero whose loadout is not the first four of their movepool, and a
  class-keyed lookup collapses a two-Highwayman party. `bridge/overlay_slots.py` holds the
  two constants so `run.py` never has to import tkinter.
- **F10 closes it, not Esc.** A click-through window cannot receive a keystroke — that is
  the point of it — so tkinter's Esc binding is dead exactly when the overlay works. The
  key is polled globally via `GetAsyncKeyState`, which rules out Esc (DD's own menu key).
- **`--calibrate`/`--test` wait `--delay` seconds before opening.** The command is typed in
  a terminal, which leaves the terminal in front of the game, and the calibration window is
  a tint over whatever is behind it.
- **Borderless is a launch option, not a menu item** — `-full_as_borderless 1`. DD's Video
  options expose only `fullscreen` and `resolution`; `full_as_borderless` lives in the
  binary's SDL window-setup block beside `vsync`/`window_pos`/`monitor_number`, and appears
  in no localization file. See `CHANGELOG.md`.
- **The layout is in SCREEN pixels, and the game's own resolution is irrelevant.** DD scales
  its 16:9 layout to fill, so 1280x720 on a 2560x1440 monitor puts the ranks in the same
  screen positions as 2560x1440 does. A change of *desktop* resolution or aspect ratio does
  invalidate it, which is what `Layout.screen` is compared against at startup.
- **The whole window is `WS_EX_TRANSPARENT`**, not just the un-drawn parts. `-transparentcolor`
  alone leaves the ring itself mouse-opaque, so the marker around an enemy eats the click
  aimed at that enemy — the overlay would block the exact action it drew.
- **Rank 1 is the front rank on both sides**, matching the engine and the game's tooltips.
  Reversing it mirrors every marker and nothing downstream objects.
- **Tk must be driven from its own thread**, so the poll loop is a daemon thread handing
  `Update`s over a `queue.Queue`. That also stops a `--survival` rollout freezing the display.
- Calibration is 9 clicks sliced 4 hero / 4 enemy / 1 card anchor, saved to
  `bridge/overlay_layout.json`. Interactive on purpose: DD's formation is laid out by the
  art, not a grid, so a formula is quietly wrong at 21:9. **The prose above said 9 clicks;
  `CALIBRATION_PROMPTS` (overlay.py:143-158) has 12** — 4 hero + 4 enemy + skill-slot-1 +
  slot-5 (MOVE) + the PASS X + the card anchor. `bridge/layout.py` migrates whatever is in
  that file into client-relative fractions losslessly, so recalibrating is not required.

## `rollout.py` — the valuation

Cutting across all three: **`rollout.py` is the valuation** — *does this party survive
that dungeon?* — answered by simulating the run N times with the checkpoint rather than
by scoring a heuristic. It is what makes the decisions OUTSIDE combat possible (`embark.py`,
the advisor's `--survival` note) at zero training cost, and it deliberately reuses
`eval_expedition.run_full_expedition_episode` so a rollout's `P(success)` is on the same
scale as an archived eval's SUCCESS. **It is a lower bound** — the probability *the policy*
finishes intact, and the policy plays worse than a competent human — so it ranks options
and must never be reported as odds.

## Invariants that must not be regressed

- **HP is written AFTER gear and trinkets, never before.** `_apply_carry_over` clamps
  `hp = min(carry.hp, max_hp)` inside `reset()` against the resolve-derived ceiling;
  `CombatEngine.apply_gear_ranks` and `health_stone` then raise it and the clamp never
  catches up. A rank-5-armour Crusader at resolve 4 opened at 54/67, and because
  `_start_next_combat` re-clamps every combat it **compounds** — a real party bleeds HP
  between fights it never took damage in. `_apply_persistent_equipment_for_current_roster(
  hp_targets=...)` and `bridge/seed.py::_sync_hp` are the two places this is enforced;
  `hp_targets=None` keeps the sampled-comp path byte-identical.
- **A real party is not a comp.** Per-hero `hero_levels`, `gear_ranks`,
  `trinkets_and_quirks`, `camp_skill_ids` and `carry_over` all have to be passed
  explicitly — a real roster has no `comp_name`. **Camp skills are not optional**: leaving
  them empty deletes the campfire from the medium and long bands and silently biases every
  comparison towards short quests.
- **Availability is `roster.building_name` on the hero record** (empty = free), not a
  search of `persist.town.json`'s buildings. `roster.status == 1` separately marks the
  four selected to embark.
- **Never read HP from a town save.** A never-deployed hero reads `current_hp` 1.0 and a
  returning one keeps the last raid's value; the game restores HP in the Hamlet, so an
  embarking party is full by definition. **Stress is the opposite** and must be carried.
- **Quest difficulty IS the mash tier** (1/3/5, matching `<zone>.conditional.1/3/5.mash.darkest`),
  and the resolve cap is real data — `campaign/quest/quest.restriction.json`'s
  `resolve_level_threshold_table` indexed by difficulty. Quest `length` 1-4 is
  Short/Medium/Long/Exhausting per the localization; Exhausting has no map-generator size.
- **Zone and tier are in `raid.raid_instance`; the enemy-line inference is now the
  fallback.** This invariant used to read "the raid save has neither", and that was wrong:
  `raid_instance` carries the entire active quest — `id`, `type`, `dungeon`, `difficulty`,
  `length`, `goal_ids` and the completion reward down to the trinket — in **399 of 399**
  captures (`bridge/route.py::read_quest`). Prefer it; where it disagrees with the guess,
  the save wins. `infer_zone_and_tier` still earns its place for a capture without a
  `raid_instance`: it scores encounter entries by **overlap**, since a real fight holds
  corpses and reinforcements, and **breaks ties towards the lower tier** — overstating
  difficulty makes retreat look more necessary than it is.
- **`Rollout` owns its own env and must never share the advisor's.** `_install` writes a
  seeded engine into the advisor's env in place; a rollout through the same instance
  overwrites the board about to be advised on.
- **`reset(seed=)` does NOT reproduce a fight.** It reseeds the combat RNG only; which
  enemy comp each combat draws comes from `ExpeditionEnv._sampler_rng`, which keeps
  advancing. Two resets with one seed face different monsters, and since `reset()`
  auto-resolves enemy turns ahead of the first hero, even the opening HP differs. Any
  comparison between parties must reseed the sampler per episode from the **scenario, not
  the party** — `Rollout.evaluate` does. Skipping it made a real embark table read a 25pp
  gap that was mostly the luck of the draw.

- **`persist.map.json` stores ids two different ways, and the graph is in the area
  names.** Areas are `rooA` / `coAB` — a corridor's name *is* its pair of endpoints, and
  nothing else in the file holds connectivity. But `entrance_id`/`final_room_id` are the
  4-char name packed **little-endian ASCII**, while `curio_prop`/`trap` are
  `dd_name_hash`; using either convention on the other field silently finds nothing.
  `final_room_id` reads `'none'` until the goal room is known. **`content` is the live
  state and `curio_prop` is the identity** — a spent curio keeps its id and drops to
  `content == 0`, so filtering on the id re-advertises 1232 already-looted tiles. And
  **`mash_index == 0` is a real index**: `t.get("mash_index") or -1` reclassifies 59
  battle tiles, which is how a survey invented a category of mash-less "boss" fights
  that does not exist. Content codes and the evidence for each are in
  `bridge/dungeon_map.py`; an unrecognised one is reported as `unknown:N` and
  `tests/bridge/test_dungeon_map.py` fails on it rather than guessing (that is how the rare
  code 9 was found, 6 occurrences against 361 of its guarded twin).
- **Whose turn it is comes from `actor.performing_turn`, NEVER from `battle.currentturn`.**
  `currentturn` is written *after* its turn resolves, so keying on it advises one turn
  late — found in live play ("when it was the Man-at-Arms's turn it still showed the
  Vestal's"). The rule is: next actor = first queue entry whose `performing_turn <
  round`, skipping the stunned. Used by `LiveState.current_actor`, `_sync_turn_pointer`
  and the advisor's poll key; regressing any one of them reintroduces the lag.
  **Scan forward from the last combatant that acted, not from the front** — the queue is
  consumed in order so the acted set is a prefix, and a combatant stunned through a round
  trails its counter permanently. When only stunned combatants remain, `current_actor`
  returns **None** and the advisor holds; falling back to `currentturn` there
  re-announced a hero who had already played.
- **Seed gear from the real `weapon_rank`/`armour_rank`, not from resolve level.** The
  engine's `tier_idx = level - 1` is a convention for sampled parties; a real roster has
  three independent numbers. Getting it wrong lowered max HP, and `_apply_carry_over`
  clamps `hp = min(carry.hp, max_hp)`, so **a damaged hero read as full**. `skill_tier`
  comes from weapon rank.
- **`trinkets.items` is keyed by SLOT, with the id inside** — unlike quirks and skills,
  which are keyed by id. Reading the keys gives `['0','1']` and equips nothing. Write
  hero HP *after* trinkets: `health_stone` raises the ceiling.
- **A corpse is `monster_class: "corpse_A"`, which is NOT a loadable monster.** Resolve
  it through `previous_monster_class`, stored as the format's name hash — otherwise
  seeding raises for the whole board and the advisor goes silent from the first kill
  onward. Its `size` decides how many ranks the corpse blocks. **Convert the hash to
  unsigned before looking it up**: DSON decodes 4-byte fields as signed and
  `dd_name_hash` is unsigned mod 2³², so every monster whose hash has the top bit set
  matched nothing and fell through to the fallback — 45% of captured corpses, silently.
- **Advise for `next_hero()`, not `current_actor`.** The save is written only when an
  action resolves, so a run of enemy turns produces no write for up to ~17 s; advising
  only for the current actor leaves stale advice on screen for that whole window. A
  look-ahead is provisional and must be labelled so: rank is not cosmetic (a skill's
  `launch` decides legality), and 46 of 246 monsters can reposition — derived from
  `move_self`/`move_target`/`push`/`pull`, not guessed. It is always superseded by a
  block built from the real post-enemy board.
- **Temporary state lives in `actor.buff_group`, and `actor.actor_dot` is a decoy.**
  `actor_dot` is empty in all 445 captured snapshots; bleed and blight are
  `buff_group` entries whose **`stat_type` is the kind** — 5 bleed, 4 blight — with
  `amount` the damage per tick, stacks already summed. **Never classify a DoT by
  `damageSourceType`**: that is the source side (5 monster-applied, 6 hero-applied), and
  reading it as the kind filed the party's own Open Vein bleed as a blight, offering
  `antivenom` against a bleed. The `stat_type` int enum was recovered by cross-referencing
  save entries that carry a real buff `id` against `shared/buffs/base.buffs.json`
  — never by assuming it is the alphabetical order of the stat types that file uses
  (that reproduces 2 and 3 by luck and misses 7, 11 and 30). Three traps, each of
  which shipped a wrong board once: **guard's marker sits on the GUARDED combatant**
  and names its guardian, not the other way round (the game's own `defender` data
  settles it — `Defender N` buffs the performer, `MAA Guard 1` marks the target);
  **`stat_type` 26 is riposte, not guard's other half** (156 occurrences against
  guard's 17, in lockstep with `riposte_on_*_chance`); and **the riposte group must
  bypass `_apply_buff_to_stats`**, since its −33% damage belongs to the
  counter-attack, not to the hero's own attacks. An unrecognised `stat_type` is
  counted into `engine.seed_unmapped_buffs` and dropped, never guessed at.
- **`reset(auto_resolve_to_hero=False)` is what makes seeding honest.** At the default,
  reset() walks the queue and lets monsters act — inventing enemy turns the real game
  already played, over the state being copied. Only `bridge/seed.py` passes it.
- **The advisor's host env is `ExpeditionSequenceEnv`, not `DarkestDungeonEnv`.** The
  full-expedition checkpoint's observation is 337 wide, not 329, and the extra 8 are
  expedition context — fed from real save data (`BATTLE_COMPLETED`, `start_heroes_size`,
  the party inventory). Reuse the env's own `_get_obs`/`action_masks`; never
  reimplement them, or the vector drifts from what the policy was trained on.
- **`live_state.py` deliberately does not use `DataLoader`** — it must stay a
  millisecond-cost pure save reader so the advisor can poll it. Its one game-file read is
  `resolve_level_thresholds`.
- **Never open the live save in place**; copy first (both `live_state.py` and
  `save_watch.py` do). `--capture` now snapshots the roster alongside the raid, so a new
  capture is self-contained — the original 72 in `archive/traces/raidsnaps/` are not, and
  `paired_persist.roster.json` is the roster they are tested against.
- **A copy that succeeded can still be a torn save, and `is_dson` will not tell you.**
  The magic is the first four bytes written, so it survives a truncation; `_stage` must
  validate with **`tools/dson.is_complete`** (the header's declared extents against the
  bytes in hand) and retry, not just retry on `OSError`. Not a rare window: `run.py`
  polls on `(mtime, size)`, so it is woken *by* the write it then races. Correspondingly
  **`_watch` must never let an exception escape** — it is a daemon thread and the
  overlay's only source of `Update`s, so raising takes the bridge silent while the human
  keeps playing and keeps believing it is watching. Report the poll, clear `last_sig`,
  carry on. See `CHANGELOG.md`.

`tools/dson.py` also exposes **`dd_name_hash`** — the format's own string hash,
`h = h*53 + c` mod 2³², recovered from a save's (name, hash) pairs, 200/200. Some ids are
stored as this hash; invert by hashing candidates. But check first: `raid.in_area` is
*not* one — it is the 4-char **area name packed little-endian ASCII**, the same convention
as the map's `entrance_id`, and it decodes to the party's current area
(`bridge/route.py::unpack_area`, 395/395 captures resolve to a real area). "Matches no
real dungeon id" was true of the hash and hid the answer for months.
