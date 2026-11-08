# The fact audit: what the decide layer acts on, and why

Started 2026-09-23 at Fran's request: *"there can be some stale facts on the
code that were made at the start of the project, so audit each take"* and
*"maybe the code has missinterpeted me, or a mechanic is not the way i thought
it was"*. **The only metric is the in-game result.**

Scope: every constant and rule the NON-combat layers act on (retreat, rollout,
torch, loot, curios, camp, route, embark, town, provisions). The combat model
is out of scope by Fran's separation rule; `engine/` has its own oracles.

Each row gives a **source**: GAME (read from a shipped file), MEASURED (driven
live and witnessed), FRAN (his own words, quoted), GUIDE (a third-party table
or site), or GUESS (a convention nobody sourced). **FRAN rows need his
confirmation**, because a transcription can misread him.

## Pass 1 findings (2026-09-23)

### A. Wrong or misattributed -- act on these

| fact | where | source claimed | what is true | in-game effect |
|---|---|---|---|---|
| Provision quantities per zone/length | `data/provisions.py::GUIDE` | "Fran's own table, the guide he plays by" | Transcribed from `provisions/provisions_guide.jpg`, a general community image (PLAYBOOK 1.5 says so). Even its MINIMUM short-Cove basket is 2,850 g, with 3-4 shovels at 250 each | Every embark spent nearly the whole wallet; with raids retreating, the campaign went broke. **Fran to rule** on early-game quantities. The obstacle count per dungeon is derivable from the game (`bridge/obstacles.py` already reads which obstacles a zone uses), so shovels need not be a guess |
| Stress priced in gold at the Abbey rate | `bridge/retreat.py` | FRAN (PLAYBOOK 0.1, 2026-09-03) | Fran 2026-09-23: stress matters only through survival | **Fixed today**: no stress in gold; deaths floored at the reward share |
| Heirlooms cannot become gold (`CREST_GOLD = 100` is a pure ordering convention) | `data/loot_loader.py`; `townplan.exchange_is_modded` | GAME (vanilla: heirloom `sell_gold_value` 0) | On Fran's install, Workshop 1679135290 adds heirloom -> gold: 6 bust / 3 portrait / 6 deed / 12 crest = 250 gold each way-in, and 2,000 gold buys them back. The wallet of 16 busts, 10 portraits, 4 deeds and 34 crests is about **2,370 gold** | The campaign embarked on 65 gold with that sitting unused, and since today the planner makes NO trades on a modded exchange. **Fran to rule**: sell heirlooms for provisions when broke? If so, the exchange rows need measuring live first (the reason trades were switched off) |
| Combat retreat always succeeds | `bridge/retreat.py` | implicit | GAME: `rules.json` `combat_retreat_chance` 0.70, +0.05 per attempt. The engine models it (`combat_engine.retreat_chance`); the retreat bill does not | A failed retreat costs a turn in a fight already judged lost. Minor since `FIGHT_RETREAT_WIPE` (0.8) |

### B. Weak evidence under a strong decision

| fact | where | problem |
|---|---|---|
| The survival forecast | `bridge/rollout.py::DEFAULT_EPISODES = 20` | SE about +-11 points at the decision edge; the live log shows RETREAT / push-on flipping between polls of one room |
| The forecast's calibration against the real game | first check 2026-09-23 | Per raid it read 17.6 deaths forecast against 3 actual, but that was a confound (raids ended early). **Per fight actually reached: forecast 3.2, actual 3, over 18 fights.** **Overruled by Fran:** the sessions were invalid (retreat-on-sight), and his counterexample is the current raid: forecast 50% intact and 1.4 deaths, and it cleared several fights with no provisions and no death. Treat the forecast as MISCALIBRATED until measured on valid raids. The 3 deaths came from the retreat-loop bug |
| `STRAINED = 50` "is the Hamlet's own line" | `bridge/embarkplan.py` | Unverified claim about the UI; the team-builder repo's 50 is independent support, not proof |

### B2. RETRACTED -- "the number that matters most"

**RETRACTED 2026-09-23 by Fran:** the sessions behind this looped at the first fight or retreated on sight, so they carry no information. The claim was: **14 live raids reached 18 fights between them, about one per raid.** Every
other lever acts on a raid that has ended after its first fight: retreats
(now repriced), stalls (fixed one at a time), no provisions (65 gold on the
new roster's first embark). Until raids routinely reach their second and
third fights, combat-model quality cannot show in the campaign.

### B3. RETRACTED -- the campaign's economy, from the journal (same invalid sessions)

Wallet at each town opening (profile_0): 5,015 -> 2,020 -> 110 (2026-09-22),
then **0 to 65 at every visit** on 2026-09-23, while the roster grew 11 -> 39.
Income exists (a completed quest paid 3,000 and the visit ended at 25), but
the provisions table spends it all on the next basket, and raids that end
after about one fight (B2) never earn it back. Three levers, all Fran's call:
smaller baskets (A, provisions), selling heirlooms (A, exchange), and fewer
retreats (fixed today).

### C. FRAN-attributed -- please confirm each is what you meant

| rule | where | as coded |
|---|---|---|
| Death floor | `retreat.FLOOR_DEATH_CHANCE = 0.5` | Retreat when P(a death) >= 50%, unless the party is worth less than the quest (scaled since today) |
| Radiant working level | `torchlight.RADIANT_FLOOR = 76`, `lootplan.TORCH_FLOOR` | Keep light >= 76 for planned fights (PLAYBOOK 2.3) |
| Treatment threshold | `townplan.TREAT_ABOVE = 50` | Treat stress above 50 when gold allows |
| Upgrade order | `townplan.UPGRADE_PRIORITY`, `MAX_RANKS_PER_VISIT = 2` | Building order and at most 2 ranks per visit |
| Heal demotion | PLAYBOOK 3.2, `advisor._apply_playbook_32` | **A hand rule on the combat model's shortlist**, which conflicts with the separation rule you gave today. Keep it or drop it? |
| Curio classes | `curios.ALWAYS_GOOD / ALWAYS_BAD` | Purge and Scouting always good; Disease, Teleport and Summon always bad |
| Dismissal | `townplan.choose_dismissals`, `reset_dismissals` | No S/A/B PizzaRat quirk AND (level 0 or below the roster mean); reset: level 0 and over 50 stress |
| Level-0 heroes are expendable | `retreat.SCALE_FLOOR_BY_INVESTMENT = True` | The death floor stands aside for a party worth less than the quest |

### D. Conventions set tonight -- unmeasured

`FIGHT_RETREAT_WIPE = 0.8`; a death priced at `max(sunk, reward / living)`;
`TOP_UP_RESERVE = 2250`; `mappanel.INK_DIM = 25`; the two-rotation PASS
fallback. Each is a guess to be measured by the live scoreboard, not defended.

### E. Verified against the game (kept for the record)

`combat_retreat_stress` 20 (`rules.json`); provision buy/sell prices and the
food floor (`base.supply.inventory.items.darkest`, `provision.json`); free
skill slots 4, or 7 for Duelist and Abomination (class files); a dead hero is
`roster.status` 3 (the save, measured); `TORCH_RESTORE` 25 (measured: four
torches take 0 to 100); the sort buttons, the dismiss flow and the help card
(measured live).

## Next pass

Loot and curio values (`data/loot_loader.py`, `CREST_GOLD = 100`, which on
Fran's install has real heirloom-to-gold rates from the exchange mod), the
camp skill choice, the route objectives, and the town economy (what a week
actually earns). Then the forecast calibration done properly: deaths per
fight reached, from the journal.

## Fran's rulings (2026-09-23)

Recorded in `docs/PLAYBOOK.md` section 7, which the code takes its rules from.
In short: retreat on at most about 20% of dungeons; always max light when
torches allow; treat only valuable heroes above about 70 stress (passive
recovery covers the rest); coach, then guild/blacksmith, then sanitarium,
then survivalist, with abbey and tavern never; curio outcomes classed as
listed; a hero worse than a fresh recruit is useless; provisions from game
averages, erring over rather than under; **never sell heirlooms** (item A2
closed: the exchange stays off); a combat retreat's fail chance belongs in
the simulator and in the decision.

## Pass 3 (2026-09-23, late)

- **Provisions derived from game data** (`bridge/provision_needs.py`). They
  mostly agree with the community table (food identical); A1's "the table
  drained the wallet" is RETRACTED, since the failing raids did. Now used item by
  item; torches are still the table's.
- **Retreat fail chance: resolved by design.** It is modelled in the engine and
  trained into the combat model; it cannot flip the gold verdict, and a failed
  attempt's lost turn is a combat decision.

## Pass 4 (2026-09-24)

- **`tile_light_loss` was read as light levels; it is tile states.** 40 clean
  live moves: a new tile costs 6 at torch 100, a re-walked one about 1.
  Planner, loot values and torch provisioning corrected (CHANGELOG
  2026-09-24). The old reading under-priced lit walking six-fold.
