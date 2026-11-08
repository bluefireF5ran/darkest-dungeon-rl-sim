# Findings: the training recipe

How the shipped recipe was found, what was measured and rejected, and
what the model has never been shown. Moved verbatim from the root
`CLAUDE.md` on 2026-09-07.


### How the 2026-08-29 recipe was found (the numbers below are 1636-era)

**One line of `train.py` did this.** `learning_rate` had been a constant `3e-4`
with no schedule since the project began; `--lr-schedule linear` decays it to
zero over the run. At a matched 12M budget, against the constant-LR checkpoint
it replaces:

| band | 12M const LR | **12M linear decay** |
|---|---|---|
| short SUCCESS / QUALITY | 96.3 / 88.3 | **97.6 / 89.5** |
| medium SUCCESS / QUALITY | 93.2 / 86.4 | **96.6 / 88.5** |
| long SUCCESS / QUALITY | 86.2 / 79.6 | **90.1 / 81.9** |
| long stress / deaths | 20.7 / 0.19 | **16.8 / 0.14** |

**THE 6M KNEE WAS AN ARTEFACT OF THE UNTUNED OPTIMISER, and this is the part to
carry forward.** Measured at constant LR, 6M → 12M was worth +0.4 SUCCESS and
+0.3 QUALITY, which is why "6M is the value point" was written down. Under the
schedule, the same step is worth **+1.6 SUCCESS and +1.9 QUALITY** — better on
all nine band-metric comparisons. **The schedule did not merely raise the
endpoint; it restored the budget's ability to pay.** A plateau measured with a
badly-sized step is a statement about the optimiser, not about the data.

| step | const LR | **linear decay** |
|---|---|---|
| long SUCCESS 6M | 85.8 / 84.0 | 87.3 / 88.5 |
| long SUCCESS 12M | 86.2 | **90.1** |
| long QUALITY 6M | 79.3 / 77.7 | 79.8 / 80.0 |
| long QUALITY 12M | 79.6 | **81.9** |

**THE RECIPE IS NOW: 12M, `--batch-size 256`, `--lr-schedule linear`.**

**And 24M is where it really does stop** (seed 1, measured after the above):
long SUCCESS **91.1** but QUALITY **81.1** and hp **72.4%** — SUCCESS +1.0,
QUALITY −0.8, both inside one standard error and **pointing opposite ways**. It
finishes marginally more runs by bringing the party home in worse shape. That
disagreement is the signature of a real plateau, and it is what 6M → 12M did
NOT look like (+1.6 / +1.9, both metrics, both seeds). Do not pay for 24M.

**Both halves are replicated on two seeds.** The **decay** at 6M against
matched constant-LR controls: eleven of twelve band-metric comparisons in one
direction (long SUCCESS +1.5 / +4.5, QUALITY +0.5 / +2.3, deaths 0.22 → 0.17
and 0.27 → 0.15). The **6M → 12M step under decay**, seventeen of eighteen:

| long band | 6M s0 | 12M s0 | 6M s1 | 12M s1 |
|---|---|---|---|---|
| SUCCESS | 87.3 | **89.6** | 88.5 | **90.1** |
| QUALITY | 79.8 | **81.6** | 80.0 | **81.9** |
| stress | 18.8 | **16.8** | 19.8 | **16.8** |
| deaths | 0.17 | **0.11** | 0.15 | **0.14** |

QUALITY moves **+1.8 and +1.9** — as consistent as this project measures
anything. **Selection warning:** the shipped arm is the better of the two seeds
on the long band, which always flatters; the honest expectation for a fresh run
of this recipe is the two-seed mean, **long SUCCESS ~89.9, QUALITY ~81.8**.


### Why decay, and why NOT a bigger network (2026-08-29)

Three PPO hyperparameters had never been tuned. One arm each at 6M seed 0
against `models/cand6M_b256.zip`:

| arm | change | clip_fraction | long SUCCESS | long QUALITY |
|---|---|---|---|---|
| control | — | 0.205 | 85.8 | 79.3 |
| A | `--net-arch 512,512` | **0.245** | **81.7** | **76.3** |
| B | `--lr-schedule linear` | **0.030** | **87.3** | **79.8** |
| C | `--n-epochs 5` | 0.132 | 84.8 | 77.4 |

**A is the instructive failure, because this file's own argument predicted a
win.** `train.py`'s comment says `[256,256]` was chosen because the first layer
was "narrower than its own input" at ~330 floats; the observation is **1652**
now, so the ratio is 6.5x — *worse* than the 5.2x that prompted the last
widening. It came back **−4.1 SUCCESS and −3.0 QUALITY**, and the diagnostics
say why: `clip_fraction` and `approx_kl` both went UP. **The wider net took
larger steps, not better ones.** Capacity was never the binding constraint;
optimisation stability was. The comment at that site now says so.

**C fails differently and the difference matters.** Halving the epochs also
lowers `clip_fraction` (0.132), and it still loses — so the ordering is *not* a
clean function of that number. What separates B from C is that B keeps all ten
epochs of gradient work and merely **sizes** the steps, while C **discards**
half the optimisation on data already collected. Stability bought by throwing
updates away is not the same as stability bought by making them smaller.

**`--batch-size 256` is the enabler throughout.** At 64 an update was 12.9 s
against 5.3 s, which is why SGD was ~73% of wall clock; it is what makes a 12M
run cost ~2.2 h. Batch alone bought only +1.6 SUCCESS / +0.1 QUALITY.

Superseded checkpoints, kept with their width and recipe in the filename:
`archive/models/dd_combat_ppo_full_expedition_1636_6M_lrdecay_s1_20260829.zip`
(long 88.5/80.0), `..._1636_12M_constlr_pre20260829.zip` (86.2/79.6),
`..._6M_b256_1636_20260828.zip`, `..._forecastv2_1636_3M_pre20260828.zip`. The
2026-08-25 checkpoint's real numbers, for anything anchored to them: short
95.3/86.5, medium 90.4/83.9, long 81.8/75.4 — **not** the 94.2/92.3/78.8 its old
header claimed, which predated several fidelity fixes.


**Every default now points at `models/`, never at
`logs/full_expedition/best_model.zip`.** That path is an `EvalCallback`
artifact, it is overwritten by the next unnamed `--full-expedition` run, and on
2026-08-25 it still held an **obs-1613** checkpoint that could not load against
a 1636-wide engine — so every bridge tool defaulting to it was silently broken.
Ship into the named slot and point tools there.

**A checkpoint is only loadable at its own observation width.** 1613, 1636 and
1644 checkpoints all exist in `models/` and none of them loads against the
current engine, which is **1652** — including
`models/dd_combat_ppo_full_expedition.zip`, the nominally shipped one. See
`tests/suite/test_obs_width_tripwire.py`. Superseded canonical copies go to
`archive/models/` with their width in the filename rather than being deleted.

**THE BRIDGE IS NOT BLIND ANY MORE, and this section said it was until
2026-09-05.** The 2026-09-04 modded retrain produced four 1652-wide
checkpoints and nobody connected them to the standing "every eval is down"
note. Use `models/dd_combat_ppo_full_expedition_mods.zip`; it loaded live and
advised a full raid.

```bash
python bridge/run.py --model models/dd_combat_ppo_full_expedition_mods.zip --survival 12
python -c "import zipfile,json,base64,re,struct,sys; z=zipfile.ZipFile(sys.argv[1]); d=json.loads(z.read('data')); r=base64.b64decode(d['observation_space'][':serialized:']); i=r.index(b'_shape'); print(struct.unpack('<H', re.search(rb'M(..)', r[i:i+24]).group(1))[0])" models/<ckpt>.zip
```

That one-liner reads a checkpoint's width without importing torch, which is
worth having: the alternative is a 454 MB import to answer one integer, and
the reason the four usable checkpoints went unnoticed for a day is that
nobody wanted to pay it.


### The training distribution: what the shipped model has never seen (2026-09-04)

The shipped checkpoint trained with `--resolve-sampling` and nothing else. So
it has **never seen a loadout other than its comp's own**, never seen a trinket
or quirk sheet other than its comp's, never fought outside its comp's tagged
region, and never fought a boss. Every one of those knobs has existed and been
`0.0` since 2026-08-20.

`--class-balance B` is the one that did not exist. The pool is skewed about
nine to one — Houndmaster fills ~100 hero slots against Duelist's 12 — so a
uniform comp draw practises one class nine times more than another.

- **A weight, never a filter.** Every comp stays reachable at every setting; a
  rare class's comps are simply drawn more often. `tests/env/test_class_balance.py`
  asserts no weight is ever zero.
- **Counted in HERO SLOTS, not comps.** A comp fielding three Crusaders
  practises the Crusader three times, and counting it once would leave exactly
  the duplicate-heavy comps under-corrected.
- **It HALVES the skew and cannot do better, which is inherent.** Measured over
  4,000 comp draws on the 273-comp pool, the most-to-least practised ratio goes
  **66.0x uniform -> 31.1x fully balanced**. A comp has four heroes, so a rare
  class cannot be drawn without also drawing its three companions, and those
  are usually common ones. Comp-level weights therefore have a floor; removing
  the rest of the skew would need per-SLOT control, which the comp structure
  does not offer. Do not read a weak result as "the dial is broken".
- **A TRAINING dial only.** Which classes are strong, and in which region, is a
  fact about the game that must keep showing up in evaluation — so nothing in
  `tools/comp_leaderboard.py` or any ruler reads it. Two different dials, and
  Fran's own framing: balance what the model *practises*, not what the
  leaderboard *reports*.
- **`0.0` is byte-identical**, and the unbalanced path deliberately keeps
  calling `rng.choice` rather than passing uniform weights to `rng.choices`.
  The two consume the generator differently, so "simplifying" that into one
  call would silently reproduce a different episode stream from every archived
  seed while looking like a refactor. A test pins it against a hand-rolled
  `choice` stream.

**`--loadout-random-p` and `--kit-random-p` want a MIDDLE value, not 1.0, and
the reason is exploration rather than representation.** Fran's objection was
that randomising kits would teach the policy that stun is unreliable: *"if i
train a model that always misses its stun against an enemy because of bad
trinkets, it will learn, i would rather deal X damage"*. Checked, and all three
terms of `P(stun) = chance + bonus − resist` are already in the observation —
the skill's stun chance is **index 0** of the 45-float payload, the enemy's six
resists were appended to the enemy slot on 2026-08-23 (its own comment: *"they
are what decides whether a stun, a debuff or a DoT is worth the turn"*), and
the attacker-side bonus is the `status_chance` bucket. So varying the kit
teaches the *conditional* rule; it cannot wash it out.

Where the objection survives is **discovery**: PPO still has to stumble into a
stun-lock before it can learn when one pays, and a kit that makes it work may
be sampled too rarely. Half the episodes on the comp's real optimised kit keeps
the strong lines discoverable, half randomised stops the kit being memorised —
hence 0.5.

And the failure he predicted is **already the status quo without any
randomisation**: `tools/style_probe.py` counts the shipped model passing over
Inspiring Cry 568 times, Open Vein 754, If It Bleeds 832 and Intimidate — an
S-tier skill — 465, trained entirely on curated kits.

**None of this has been MEASURED yet.** The knobs are wired, defaulted off and
tested; no arm has been run. Two seeds, per the standing rule.


### The distribution bundle is HARMFUL — measured, two seeds (2026-09-05)

`--loadout-random-p 0.5 --kit-random-p 0.5 --class-balance 1.0`, 12M each
against the matched no-knob checkpoints on the same corpus and recipe:

| SUCCESS | short | medium | long |
|---|---|---|---|
| control s0 | 95.7 | 92.0 | **78.5** |
| knobs s0 | 94.5 | 91.8 | **73.3** |
| control s1 | 96.1 | 93.8 | **78.0** |
| knobs s1 | 95.1 | 92.9 | **74.0** |

**Long band −5.2 and −4.0. Six of six band comparisons negative.** Not a null
like `--prevention-reward`: actively worse, and replicated.

**The eval is the deployment case, and that is the point rather than a
caveat.** A policy trained on randomised kits is scored on the comps' OWN
kits, because `ExpeditionSequenceEnv.reset`'s explicit-scenario path bypasses
those knobs. So the arm is off-distribution at eval — and that is exactly the
situation it would be in when playing Fran's curated comps, which is what the
bridge does. If the bundle is ever revisited, the claim to test is robustness
on a randomised-kit EVAL, which no harness here currently produces.

The threshold argument still stands and is untouched by this: all three terms
of `P(stun) = chance + bonus − resist` are observable, so varying the kit
teaches the conditional rule. What the measurement says is that whatever it
teaches is not worth what it costs in practice on curated comps.

Defaults stay 0.0. `--class-balance` also stays, unmeasured on its own — the
bundle cannot say which of the three did the damage, and decomposing it is
four more experiments across two seeds (~17 hours).


## `--boss-quest-p 0.25`: it wins where it trained, not in a campaign (2026-10-10)

The shipped recipe plus `--boss-quest-p 0.25`, 12M, seed 0 (146 min,
`models/boss25_s0.zip`, log `archive/logs/train_boss25_s0_20261010.log`).
The 2026-10-06 attempt at the same thing died at 660k steps and left nothing.

**The N2 rulers say ship it** (`--all-comps --episodes 2`, same engine for both):

| band | corridor shipped -> boss25 | `--boss-quests` shipped -> boss25 |
|---|---|---|
| short | 97.8 -> 97.9 | 87.0 -> 89.5 |
| medium | 96.0 -> 96.9 | 83.1 -> 87.1 |
| long | 85.8 -> 88.1 | 72.2 -> 79.5 |

**The campaign simulator says no** (`docs/CAMPAIGN_SIM.md`, E6): +9.3 and
+9.5 dead per 60-week campaign on two seed blocks (3.3 and 4.2 SE), +3 wipes.

Two instruments disagreeing, so the same 540 campaign raids (95 bosses) were
replayed under both models:

| raids as | deaths/raid shipped -> boss25 | boss success shipped -> boss25 |
|---|---|---|
| played (campaign heroes) | 1.17 -> 1.27 (+0.11 +- 0.08) | 20% -> 21% |
| best gear for their level | 0.79 -> 0.89 | **61%** -> 57% |
| the training world (level, gear, kit, trinkets) | 0.10 -> 0.08 | 81% -> 85% |

**The gain is real in the world it trained in and does not transfer** to the
undergeared, underlevelled parties a campaign actually fields, where it
presses on and loses more heroes. And the campaign's boss losses are not the
policy's at all: the SAME shipped policy wins 61% instead of 20% once the
heroes have the gear their level allows. The lever is the Hamlet's gold, not
the combat model. One training seed: a second would be needed before
claiming anything about boss exposure in general.

