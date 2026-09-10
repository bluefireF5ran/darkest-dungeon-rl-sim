# Findings: modded classes

Loading third-party classes with their kit, and the gate deciding which
may enter the training corpus. Moved verbatim from the root
`CLAUDE.md` on 2026-09-07.


### A third-party class loads with its KIT (2026-08-27) — the Sibyl

`DDSIM_MOD_DIRS` is an `os.pathsep`-separated list of content roots, off unless
set. It used to buy the hero and nothing the hero does: `effects/`,
`shared/buffs/`, `trinkets/` and `raid/camping/` were read from `GAME_DIR`
alone, so **150 of the Sibyl's 160 effect references (93.8%) resolved to
nothing** — the three `switch_mode_*_self` effects among them, which is her
whole class. She could not leave `moon` and five of her seven skills were
permanently illegal, silently. `DataLoader`, `CampingLoader` and the
localization scan now take the mod roots too: **0 of 160 unresolved**, and ten
of the twelve subscribed Workshop classes resolve 100%. The two that do not
(`Navigator`, `wanderer`) throw inside `EffectLoader` on malformed lines in
their own files — mod-data breakage, not a path problem.

- **A mod may ADD an id, never SHADOW one** (`data/mod_roots.py::merge_new_only`).
  The DLC merge one block above uses `dict.update()` and that is fine for
  first-party content; a mod redefining `Stun 1` would silently change what
  every vanilla comp does, with no error and no missing id.
- **The store cache keys on `DDSIM_MOD_DIRS` now**, both the roots' files and
  the variable's own value. The old advice — pair it with
  `DDSIM_NO_STORE_CACHE=1` — cost a full rebuild in each of a search's twelve
  workers. Mods on and mods off are simply different cache entries.
- **`UNRESTRICTED_HEROES` is DERIVED, not listed.** The game states it:
  `skill_selection: .can_select_combat_skills`, shipped `false` on exactly the
  Abomination and the Duelist and `true` on the other eighteen. The derivation
  reproducing that pair **is** the check that it reads the right key. The
  constant stays and stays exported (a dozen tools import it); prefer
  `unrestricted_heroes(store)` wherever a store is in hand. Cut to a 4-of-7 the
  Sibyl can lose `align` and be stranded in one stance for the whole raid.
- **A comp can name a modded class now.** `HERO_DISPLAY_NAME_TO_ID` is twenty
  hand-written rows and an unrecognised `heroClass` is dropped SILENTLY, so a
  comp fielding one trained as a smaller party. `_hero_class_map(store)` keeps
  those rows first and authoritative and adds what the store loaded underneath,
  by id and by title form — it can only rescue a slot that was being dropped.
- **The observation is still 1636 wide with a 21st class, and every checkpoint
  still loads.** That is the mechanics-not-identity rewrite paying off: a new
  class is new payload values in slots that already exist. It does not buy
  competence — `--hold-out-class` measured +46.9pp excess gap for an unseen
  class — so a Sibyl comp needs a retrain, not just a loader.
- **Nothing in `engine/` changed for three stances.** `.valid_modes`,
  `SkillLevelData.mode_effects` and `.set_mode` have been there since the
  Duelist. `align` cycles moon → eclipse → sun at `.per_turn_limit 1` and
  `.is_continue_turn true`, so she aligns once per turn and keeps the turn.
  `tests/data/test_mod_kit.py` drives a real fight and requires all three stances
  with their own legal sets.

**THE SUITE IS A MODS-OFF SUITE, and that is the contract rather than a gap.**
Ten tests assert vanilla counts — 80 camping skills, 24 shuffle effects, one
district tag per hero, the comp pool size — and every one is *supposed* to fail
under `DDSIM_MOD_DIRS`, because the flag changes the data those numbers
describe. That is what keeps the default store reproducible and every archived
measurement re-readable. `tests/data/test_mod_kit.py` is the exception and builds
both stores explicitly, so it is right whichever way the environment points.
Run the suite without the variable.

**A comp naming a class this store cannot load is EXCLUDED, not repaired.**
`build_comp_pool` drops any comp with `len(heroes) < declared_heroes`, which is
the structural filter that replaced the old `.md` name list — so a Sibyl comp
simply is not in the pool when mods are off, and is when they are on.
`CompLoadoutEntry.declared_classes` records WHICH classes were asked for, which
is what tells "fields a class we cannot load" (fine) apart from "a name map
dropped a class we DO load" (a silent bug). `tests/data/test_loader.py` uses it to
keep that distinction.

**`tools/compose_class_comps.py` gives a class with NO comps a training pool.**
`build_comp_pool` iterates `store.comp_loadouts`, so a class in no comp file is
never sampled. Three of four slots are copied verbatim from a curated comp and
only one is replaced; the invented block is all seven skills (she cannot
select), her own camp skills first, two class-locked unlimited trinkets and
**no quirks** — an invented quirk sheet would flatter every measurement made on
these comps. Output goes to `composed_comps/`, which is training coverage and
never a proposal.

```bash
DDSIM_MOD_DIRS="D:/…/workshop/content/262060/3490076588" \
  python tools/compose_class_comps.py --class sibyl_ms --count 4 --dry-run
```

**40 curated trinket slots equip NOTHING, and 38 are two typos** — found by the
composer on its first run, in the source comps rather than its own output.
`Hunter's Talons` (23 slots) is `Hunter's Talon`; `Tempting Goblet` (15) is
`The Tempting Goblet`; `Cloak and Dagger` and `Eerie Eye` have no near match.
**Not guessed at in code**: plural-to-singular is a semantic guess, and the
orthographic net deliberately stops short of those — the DEBUFF RES precedent
is that a guessed fix does not ship.


### 50 Workshop classes, and the gate that decides which may train (2026-09-04)

Fran subscribed 50 community classes and asked that the policy learn to play
them. **48 load, 29 pass the gate, 27 have a training corpus** — and the
observation stays **1652 wide**, so the shipped checkpoint still loads. This
is a corpus job, not an engine one, exactly as the Sibyl predicted.

**A class can load, resolve every effect id, and still be INVISIBLE.** The
policy reads a skill through the 45-float payload in `_mechanics_from_data`;
an all-zero payload is a bare attack with no mechanic on it.

| | skills with an all-zero payload |
|---|---|
| vanilla | 12 of 140 (**9%** — genuine plain attacks, the calibration baseline) |
| modded | 126 of 348 (**36%**) |

Five classes are 100% invisible (Kuuga 14/14, Mordekaiser 10/10, forlorn,
duchess, EosOceanDragonMaid). **Training on those is worse than omitting
them**: the policy is rewarded for outcomes it cannot attribute to anything it
perceives, and it would poison the pool-scale transfer experiment the corpus
exists to run.

```bash
python tools/class_visibility.py --mods "<root>;<root>" --json out.json
python tools/compose_class_comps.py --from-gate out.json --count 4
```

`tools/class_visibility.py` is a RULER (import-guarded in
`tests/suite/test_rulers.py`) and refuses three ways to be invisible:

- **fewer than 5 of 7 skills carrying a payload** — set against vanilla's 9%
  blind rate, so a class is not suspect for one or two plain attacks;
- **more than 10% of effect references unresolved** — a SHARE, not a count,
  and the cut sits in a measured gap: tooltip-only classes read 2.9% and 7.4%,
  genuinely broken ones 17.2%, 52.4% and 100%. Nothing falls between;
- **more skills than `MAX_SKILL_SLOTS`** — actions `0..27` are
  `slot(7) * rank(4)`, so a 9th skill has no index and can never be chosen.
  Four Workshop classes ship 9 to 14; `MAX_SKILL_SLOTS`'s own comment ("every
  real hero class has exactly 7 skills") is now false.

**THREE LOADER BUGS, EACH SILENT, EACH COSTING WHOLE CLASSES:**

- **A mod's string table may LIE about its own encoding.** Three files declare
  `encoding="UTF-8"` and are not (`seraph`: 0xce at offset 227,850).
  `_load_english_entries` caught `UnicodeDecodeError` and `continue`d, so ~600
  entries vanished with no error — which surfaced two layers away as
  `build_hero_block` refusing the class because "its trinkets do not resolve".
  Fixed by `data/text_io.py`, an ordered decode chain ending in `latin-1`,
  which cannot fail. **A valid UTF-8 file takes the first branch and is
  byte-identical**, so the vanilla store is untouched.
- **The same, fatally, in `DarkestParser`.** A strict read there aborted
  `DataLoader.load_all` for the WHOLE store, so one GBK mod took every class
  down with it. `lyzm` and `mtgh` load now.
- **`build_hero_block` demanded two UNLIMITED class trinkets.** Written
  against the Sibyl, who is the exception: **18 of the 29 gate-passing classes
  have 7-13 class trinkets and not one unlimited**, because a class trinket
  being one-per-estate is the ordinary case. It now falls back
  unlimited → one-off → generic, and a one-off is safe because `compose`
  already refuses a party wearing the same one twice. **0 → 27 classes.**

**The corpus: 267 comps, 47 classes** (20 vanilla + 27 modded, median 3 hero
slots each — the dosage that took the Sibyl from a −28% swap cost to −1%).
Every comp resolves 4/4 heroes. Zone skew got worse, not better: crypts 55%,
because a composed comp inherits its source's region.

**THE EXPERIMENT RAN, AND CROSS-CLASS TRANSFER IS CLOSED FOR THE FIFTH AND
LAST TIME (2026-09-04).** Four mechanisms had failed and all four changed the
MODEL; this changed the DATA — hold a class out of **47** instead of 19, so
memorising the pool stops being the cheap solution. It also met the second
condition the standing rule sets, because the premise none of the four had
checked is now measured: **a skill's nearest neighbour in payload space is four
times closer in a DIFFERENT class (0.255) than in its own (1.032)**, 28% have a
near-exact twin elsewhere and 55% a close cousin. The shared structure is real,
rich, encoded, and exposed to a head built to exploit it.

It still failed, on both seeds:

| held out `sibyl_ms`, 3M vs 3M | seed 0 | seed 1 |
|---|---|---|
| control gap (seen -> held-out) | −11.2pp | −15.4pp |
| held-out arm's gap | +11.3pp | +2.7pp |
| **paired excess gap** | **+22.5** [+13.0, +32.7] | **+18.1** [+7.6, +30.3] |

**Mean of seeds +20.3, between-seed SD 3.1** — the effect is 6.5x the spread,
which is what a replicated result looks like here; `--stress-weight` read +2.33
and +0.27 and had to be reported as unreplicated.

**Read the excess gap, never the absolute rates.** On both seeds the CONTROL
scores BETTER on the held-out class's comps than on seen ones — hers are
easier — so the held-out arm's 57.3% / 65.4% looks unremarkable until you know
a trained policy gets 76.5% / 82.3% on the same comps.

Do **not** read +20.3 against the archived +46.9 as "pool scale halved it":
different held-out class, pool and fidelity. **Few-shot per class is the
permanent answer, and this corpus is it. Do not open a sixth attempt.**
