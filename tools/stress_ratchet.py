"""Does stress ever come back DOWN? — the simulator against real play.

Fran, 2026-09-03, on watching a raid fall apart: *"when a hero starts panicking
usually its like a house of cards in a sense"*.

Measured over his four recorded sessions, that is **not** what the data shows,
and the correction is the interesting part.

**It does not accelerate.** Rises from under 50 average **+13.1** a turn; rises
from 50 or over average **+10.6** — slightly slower, not faster. So "cascade" is
not the mechanic.

**And it is not strictly irreversible either**, which is the correction this
tool made to its own first reading. Stress comes down 34 times in the corpus,
and 1 of the 7 heroes who crossed 50 got back under it. What is lopsided is the
SIZE: falls average **−4.4** against rises of **+12.4**, so recovery exists and
is worth about a third of a single bad turn.

That is the shape worth naming — a **ratchet**: reversible in principle, priced
so that it never keeps up in practice. This tool asks whether the simulator
reproduces it.

## Why it matters more than it looks

Stress is half of `_run_quality` and the second thing Fran ranks, and
`--stress-weight` — the one knob aimed straight at it — came back as a clean
HP-for-stress trade at par, buying no score. **If the simulator lets a hero walk
back down from 80 stress and the real game effectively does not, that trade is
an artefact of the simulator and the knob was measuring the wrong world.**

## Reading it

`P(recover)` is the headline: of the heroes who ever crossed the threshold, the
share who were ever seen below it again. The two rise rates are the cascade
check — if they are close, "it accelerates" is not the mechanic.

**The real-play arm is SMALL.** It is whatever `bat/advise.jsonl` holds — 130
hero turns at the time of writing — and it samples the party once per recorded
decision. The simulator arm samples once per env step. Neither is a rate per
unit of game time, so compare the two SHAPES, not the two numbers, and treat a
disagreement as somewhere to look rather than as a measured effect.

**A gap here is a finding, not a fix.** Do not move a stress constant on this
evidence; record it with its falsifier, the way the DEBUFF RES gap was.

## And a second mechanic: FIGHT LENGTH -- measured, then RETRACTED

Duration is what makes a buff, a control skill or a stress heal worth spending
a turn on (PLAYBOOK 3.5), so a simulator whose combats are systematically
shorter would price every one of them too low.

It first read as exactly that: real play **3.9 rounds** with **50%** past
round 3, the simulator **2.0** and **9.2%**. Controlled, it goes away. The
real sample pooled 4-hero combats with a 2-hero one and two where a hero fell
mid-fight; the simulator arm pooled every tier and never lost anybody. Matched
on both -- `--arms B` -- the simulator gives **2.68 [2.3, 3.1]** against real
4-hero-intact **3.00 [2.3, 3.9]**, and the intervals overlap across most of
their width.

Party size is the effect that was hiding in it: the same comps cut to two
heroes give **4.19 [3.4, 5.2]**. The "independent replication" of 3.9 from an
agent session was a two-hero tutorial party, so it replicated party size.

**Read the >3 SHARE, not the mean**, and read the interval: ten combats cannot
support a bare mean and this comparison was quoted as one for a day.

**And treat every interval as too narrow.** They are bootstrapped over combats
as if fights were independent; fights share an episode and episodes share a
comp, and comp identity moves length a lot at rank 0. Arm B at 40 episodes
reads 2.68 [2.3, 3.1] and at 5 episodes on another seed 1.60 [1.3, 1.9] -- two
non-overlapping intervals for one arm. Direction and magnitude, never a
decimal.

A **RULER**: nothing under `engine/`, `env/`, `data/`, `bridge/` or `train.py`
may import it.

    python tools/stress_ratchet.py                       # sim + bat/advise.jsonl
    python tools/stress_ratchet.py --episodes 40
    python tools/stress_ratchet.py --journal <file> ...  # pool other recordings
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Where "in trouble" starts. 50 is half the Resolve Check, which is the point
#: a real player starts steering for the Abbey — and it is the threshold the
#: real-play observation was made at, so moving it breaks the comparison.
THRESHOLD = 50.0


def _series_stats(series, threshold=THRESHOLD) -> dict:
    """`series` is a list of per-hero stress lists, each in time order."""
    crossed = recovered = 0
    lo_rises, hi_rises, falls = [], [], []
    for stresses in series:
        ever_high = False
        high_then_low = False
        for a, b in zip(stresses, stresses[1:]):
            if a >= threshold:
                ever_high = True
            if ever_high and b < threshold:
                high_then_low = True
            delta = b - a
            if delta > 0:
                (hi_rises if a >= threshold else lo_rises).append(delta)
            elif delta < 0:
                falls.append(-delta)
        crossed += ever_high
        recovered += high_then_low
    return {
        "heroes": len(series),
        "crossed": crossed,
        "recovered": recovered,
        "p_recover": (recovered / crossed) if crossed else None,
        "rise_lo": statistics.mean(lo_rises) if lo_rises else 0.0,
        "rise_hi": statistics.mean(hi_rises) if hi_rises else 0.0,
        "n_lo": len(lo_rises),
        "n_hi": len(hi_rises),
        "falls": len(falls),
        "fall_mean": statistics.mean(falls) if falls else 0.0,
    }


# --- how sure are we? ------------------------------------------------------
#
# Ten combats cannot support a bare mean, and this comparison has been quoted
# as one all week. Every arm is reported the same way so the real side's
# interval is visible beside the simulator's rather than implied.

#: A fight "lasts" if it goes past this many rounds. PLAYBOOK 3.5's rule is
#: about duration, and this is the share the rule actually turns on -- far more
#: robust at n=13 than a mean over a long-tailed count.
LONG_ROUNDS = 3
BOOTSTRAP_ITERS = 4000


def _mean(xs) -> float:
    return (sum(xs) / len(xs)) if xs else 0.0


def _long_share(xs) -> float:
    return (sum(1 for x in xs if x > LONG_ROUNDS) / len(xs)) if xs else 0.0


def bootstrap_ci(values, stat, iters: int = BOOTSTRAP_ITERS, seed: int = 0,
                 alpha: float = 0.05):
    """Percentile bootstrap interval for `stat`, or `(None, None)`.

    Percentile rather than normal-theory because the statistics here are a
    mean over a long-tailed count and a proportion near its own boundary --
    neither is symmetric at n=10, which is exactly the sample this exists for.

    Fewer than two values gets `(None, None)` rather than a degenerate zero-
    width interval: one combat is not evidence about the spread of combats,
    and a printed `[3.9, 3.9]` would claim it is.
    """
    vals = list(values)
    if len(vals) < 2:
        return (None, None)
    rng = random.Random(seed)
    n = len(vals)
    draws = sorted(stat([vals[rng.randrange(n)] for _ in range(n)])
                   for _ in range(iters))
    lo = draws[int(alpha / 2 * iters)]
    hi = draws[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (lo, hi)


def _ci_text(lo, hi, pct: bool = False) -> str:
    if lo is None:
        return "[too few to say]"
    return (f"[{lo:.0%}, {hi:.0%}]" if pct else f"[{lo:.1f}, {hi:.1f}]")


# --- how LONG is a fight? ---------------------------------------------------

def _enemy_key(row) -> frozenset:
    """Which enemies are on the board, for telling two combats apart."""
    board = row.get("board") or {}
    return frozenset(str(e.get("id") or e.get("name") or "")
                     for e in board.get("enemies") or [])


def combat_lengths(series_source: str, rows=None, plays=None) -> list:
    """Rounds reached per combat, from journal `play` events.

    A combat boundary is the round counter going BACKWARDS -- `round` restarts
    at 1 in every fight and only ever climbs within one, which is the same
    boundary `bridge/run.py::_resolve` uses to drop a stale recommendation.

    **That rule alone MERGES two consecutive one-round fights**, because 1 is
    not less than 1. It is not a rare shape here: the simulator's own median
    combat is two rounds. Merging drops a short fight from the count, which
    raises the mean -- the direction that would flatter the very claim this
    tool is testing. So a wholly DIFFERENT set of enemies is a boundary too:
    the board cannot swap every occupant without the fight having ended.

    Enemy identity is only used when both sides carry one. A journal that
    records no enemies falls back to the round rule and is no worse than
    before.
    """
    return [c.rounds for c in combat_records(plays)]


@dataclass(frozen=True)
class Combat:
    """One fight, as a journal recorded it."""
    rounds: int
    party_min: int
    party_max: int

    @property
    def lost_someone(self) -> bool:
        """Did the party shrink mid-fight?

        Worth separating rather than averaging in. The two longest combats in
        the real corpus (8 and 6 rounds) are both ones where a hero fell, and
        a fight that continues short-handed is the SAME mechanism arm C
        isolates -- fewer heroes, less damage, more rounds -- operating inside
        a single combat instead of across a party.
        """
        return self.party_min < self.party_max


def combat_records(plays) -> list:
    """Per-combat rounds and party size, sharing one boundary rule."""
    out, cur, last, last_foes = [], None, -1, frozenset()

    def close(rows):
        sizes = [len((r.get("board") or {}).get("heroes") or []) for r in rows]
        sizes = [n for n in sizes if n] or [0]
        return Combat(max(r.get("round", 0) for r in rows),
                      min(sizes), max(sizes))

    for r in plays or []:
        rd = r.get("round")
        if rd is None:
            continue
        foes = _enemy_key(r)
        swapped = bool(foes) and bool(last_foes) and not (foes & last_foes)
        if cur is None or rd < last or swapped:
            if cur:
                out.append(close(cur))
            cur = [r]
        else:
            cur.append(r)
        last, last_foes = rd, foes
    if cur:
        out.append(close(cur))
    return out


def _by_party_report(label: str, records, seed: int = 0) -> None:
    """The real arm, split the way the simulator arms are constructed.

    Arm B is four heroes and arm C is two, so an unsplit real mean is being
    compared against two different worlds at once -- which is how a 3.9 that
    is mostly 4-hero combats came to be quoted beside a 2.0 and an agent
    sample that was entirely 2-hero.
    """
    intact = [c for c in records if not c.lost_someone]
    print(f"  {label}  by party size (combats where nobody fell)")
    for size in sorted({c.party_max for c in intact}):
        rounds = [c.rounds for c in intact if c.party_max == size]
        lo, hi = bootstrap_ci(rounds, _mean, seed=seed)
        print(f"    {size} heroes   n={len(rounds):3d}   "
              f"mean {_mean(rounds):5.2f}  {_ci_text(lo, hi)}   "
              f">{LONG_ROUNDS} rounds {_long_share(rounds):5.1%}")
    fell = [c for c in records if c.lost_someone]
    if fell:
        print(f"    a hero FELL mid-fight in {len(fell)} more: rounds "
              f"{sorted(c.rounds for c in fell)} -- the same mechanism arm C "
              f"isolates, inside one combat")
    print()


def _rounds_report(label: str, lengths, seed: int = 0) -> None:
    """One arm, with an interval on both statistics.

    The SHARE is printed last and is the headline: it is what PLAYBOOK 3.5's
    rule turns on, and it survives a sample this small far better than a mean
    over a long-tailed count.
    """
    if not lengths:
        print(f"  {label}: no combats\n")
        return
    lengths = sorted(lengths)
    med = lengths[len(lengths) // 2]
    m_lo, m_hi = bootstrap_ci(lengths, _mean, seed=seed)
    s_lo, s_hi = bootstrap_ci(lengths, _long_share, seed=seed)
    print(f"  {label}")
    print(f"    combats {len(lengths):5d}   median {med:3d}   max {lengths[-1]:3d}")
    print(f"    mean rounds        {_mean(lengths):5.2f}  {_ci_text(m_lo, m_hi)}")
    print(f"    lasting >{LONG_ROUNDS} rounds  {_long_share(lengths):5.1%}  "
          f"{_ci_text(s_lo, s_hi, pct=True)}")


def from_journal(paths) -> list:
    """Per-hero stress series out of `bridge/journal.py` `play` events.

    Keyed on `(guid, class)` and NOT reset per raid: a hero carries stress out
    of the dungeon, which is the whole reason stress is the quantity Fran ranks
    second. Pooling across raids is therefore the faithful reading, not a
    convenience.
    """
    by_hero: dict = {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") != "play":
                continue
            for hero in row.get("board", {}).get("heroes", []):
                key = (hero.get("guid"), hero.get("class"))
                by_hero.setdefault(key, []).append(float(hero.get("stress", 0.0)))
    return [s for s in by_hero.values() if len(s) > 1]


# --- the three simulator arms ----------------------------------------------
#
# The fight-length comparison has always been confounded: both real samples
# are low-level APPRENTICE parties and the simulator arm pools every tier.
# Weak heroes kill slowly, which lengthens fights all by itself, so "the
# simulator's fights are half as long" and "the simulator was scored on a
# harder party" predict the same table.
#
# `bat/advise.jsonl`'s `raid_started` events say exactly which world the real
# combats came from -- `{"dungeon": "crypts"|"weald", "difficulty": 1,
# "length": 1}` -- so it can be reproduced rather than approximated.

#: The classes the real corpus actually fielded, so a drawn party is one that
#: could have been his. Taken from `bat/advise.jsonl`'s own `play` boards.
REAL_CLASSES = ("crusader", "highwayman", "occultist", "antiquarian",
                "plague_doctor", "shieldbreaker", "runaway")
#: A comp as written. Cutting one to `Arm.party_size` keeps ranks 1..N.
FULL_PARTY = 4


@dataclass(frozen=True)
class Arm:
    """One scenario to measure fight length in.

    `tier=None` is the SAMPLED path -- the arm that produced the 2.0 this
    started from -- and it must stay reachable unchanged, or the new arms have
    nothing to be compared against.
    """
    key: str
    label: str
    tier: Optional[int] = None
    zones: tuple = ()
    band: str = "short"
    party_size: int = 4
    classes: tuple = ()


ARMS = (
    Arm("A", "sampled tier and band, as measured before"),
    Arm("B", "tier 1 / short / crypts+weald / 4 heroes at resolve 0, rank 0",
        tier=1, zones=("crypts", "weald"), party_size=4, classes=REAL_CLASSES),
    Arm("C", "as B but cut to the FRONT TWO heroes -- party size alone",
        tier=1, zones=("crypts", "weald"), party_size=2,
        classes=REAL_CLASSES),
)
ARMS_BY_KEY = {a.key: a for a in ARMS}


def parties_for(arm: Arm, store) -> list:
    """Real curated parties, cut to `arm.party_size` and stripped to rank 0.

    Drawn from `curated_comps/` rather than invented, so the heroes carry the
    skills, trinkets and quirks a player brought -- Fran's did, and kit drives
    damage, which drives length. A bare class list would understate damage and
    lengthen the fights, which is the direction that would flatter the claim
    being tested.

    Resolve and gear ARE overridden, because that is the axis under test.
    Note they have to be set here: the five randomisation knobs (including
    `resolve_sampling`) are unreachable from an explicit scenario, so nothing
    else would apply them.
    """
    from bridge.rollout import PartyMember                # noqa: PLC0415

    allowed = set(arm.classes)
    out = []
    for name in sorted(store.comp_loadouts):
        entry = store.comp_loadouts[name]
        heroes = entry.heroes
        # **The WHOLE comp has to be in the set, and its ORDER is kept.**
        # Filtering heroes out of a comp and closing ranks was the first cut
        # and it is wrong twice over: `CombatEngine.reset` assigns
        # `position = index`, so dropping a hero moves everyone behind them
        # forward into ranks their skills may not launch from -- and the comps
        # that survived it were the degenerate ones (four Runaways), because a
        # comp made entirely of in-set classes is rarer than a comp with four
        # of them somewhere.
        if len(heroes) != FULL_PARTY:
            continue
        if not all(h.hero_id in allowed for h in heroes):
            continue
        # Cutting to the FRONT ranks is what makes arm C a controlled contrast
        # rather than a different experiment: B and C draw the same comps and
        # differ only in how many heroes are standing.
        party = []
        for h in heroes[:arm.party_size]:
            quirks = h.quirk_ids
            flat = ([q for v in quirks.values() for q in v]
                    if isinstance(quirks, dict) else list(quirks))
            party.append(PartyMember(
                hero_class=h.hero_id, resolve_level=0,
                weapon_rank=0, armour_rank=0,
                skill_ids=tuple(h.skill_ids),
                camp_skills=tuple(h.camp_skill_ids),
                trinkets=tuple(h.trinket_ids), quirks=tuple(flat)))
        out.append((name, party))
    return out


def from_simulator(model_path: str, episodes: int, seed: int = 0,
                   arm: Optional[Arm] = None):
    """Per-hero stress series and per-combat lengths over expeditions.

    One stress series per hero PER EPISODE — a fresh expedition draws a fresh
    party, so concatenating them would invent a recovery at every episode
    boundary, which is precisely the statistic being measured.

    **Combat boundaries come from `_combat_idx`, not from the round counter.**
    The round rule cannot separate two consecutive one-round fights, and at a
    median of two rounds those are common here. The journal has no such index
    and has to infer it; this side does not, and using the exact answer where
    it exists keeps the simulator's own number honest.
    """
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.utils import get_action_masks

    from bridge.rollout import build_options              # noqa: PLC0415
    from env.expedition_sequence_env import ExpeditionSequenceEnv

    arm = arm or ARMS[0]
    env = ExpeditionSequenceEnv(sampler_seed=seed,
                                resolve_sampling=arm.tier is None)
    model = MaskablePPO.load(model_path, device="cpu")
    parties = parties_for(arm, env.store) if arm.tier is not None else []
    if arm.tier is not None and not parties:
        raise SystemExit(f"arm {arm.key}: no curated comp fields "
                         f"{arm.party_size} of {', '.join(arm.classes)}")
    out: list = []
    lengths: list = []
    for ep in range(episodes):
        if arm.tier is None:
            env.seed_scenario(seed + ep)
            obs, _ = env.reset(seed=seed + ep)
        else:
            _name, party = parties[ep % len(parties)]
            options = build_options(party)
            options.update(tier=arm.tier, length_band=arm.band,
                           zone_id=arm.zones[ep % len(arm.zones)],
                           sampler_seed=seed + ep)
            obs, _ = env.reset(seed=seed + ep, options=options)
        tracks: dict = {}
        per_combat: dict = {}
        term = trunc = False
        steps = 0
        while not (term or trunc) and steps < 3000:
            idx = int(getattr(env, "_combat_idx", 0))
            rd = int(getattr(env.engine, "round", 0))
            per_combat[idx] = max(per_combat.get(idx, 0), rd)
            for hero in env.engine.heroes:
                tracks.setdefault(id(hero), []).append(float(hero.stress))
            action = int(model.predict(obs, deterministic=True,
                                       action_masks=get_action_masks(env))[0])
            obs, _r, term, trunc, _i = env.step(action)
            steps += 1
        out.extend(v for v in tracks.values() if len(v) > 1)
        lengths.extend(per_combat[k] for k in sorted(per_combat))
    return out, lengths


def _report(label: str, st: dict) -> None:
    print(f"  {label}")
    print(f"    heroes tracked         {st['heroes']:5d}")
    print(f"    ever above {THRESHOLD:.0f}          {st['crossed']:5d}")
    if st["p_recover"] is None:
        print("    came back below        n/a — nobody crossed")
    else:
        print(f"    came back below        {st['recovered']:5d}"
              f"   P(recover) {st['p_recover']:.0%}")
    print(f"    mean rise under {THRESHOLD:.0f}     {st['rise_lo']:+6.2f}"
          f"   (n={st['n_lo']})")
    print(f"    mean rise at/over {THRESHOLD:.0f}   {st['rise_hi']:+6.2f}"
          f"   (n={st['n_hi']})")
    print(f"    downward moves         {st['falls']:5d}"
          f"   mean -{st['fall_mean']:.2f}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--episodes", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--journal", action="append", default=[],
                    help="recording(s) to pool (default: bat/advise.jsonl)")
    ap.add_argument("--no-sim", action="store_true",
                    help="real play only, for a quick re-read of the corpus")
    ap.add_argument("--arms", default="A",
                    help="which simulator arms to run: A (sampled, the "
                         "baseline), B (tier 1 / short / 4 heroes at rank 0), "
                         "C (B cut to two heroes), 'all', or a comma list")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    paths = [Path(p) for p in args.journal] or [root / "bat" / "advise.jsonl"]
    paths = [p for p in paths if p.is_file()]

    print(f"Does stress come back down? (threshold {THRESHOLD:.0f})\n")

    if paths:
        real = from_journal(paths)
        if real:
            _report(f"REAL PLAY  ({', '.join(p.name for p in paths)}, "
                    f"one sample per recorded decision)", _series_stats(real))
        else:
            print("  REAL PLAY  no `play` events in the journal\n")
    else:
        print("  REAL PLAY  no journal found — run bat/combat_advise.bat\n")

    wanted = ([a.key for a in ARMS] if args.arms.strip().lower() == "all"
              else [k.strip().upper() for k in args.arms.split(",") if k.strip()])
    unknown = [k for k in wanted if k not in ARMS_BY_KEY]
    if unknown:
        print(f"  unknown arm(s): {', '.join(unknown)}")
        return 2
    arms = [ARMS_BY_KEY[k] for k in wanted]

    sim_lengths: list = []
    if not args.no_sim:
        for arm in arms:
            sim, lengths = from_simulator(args.model, args.episodes,
                                          args.seed, arm)
            _report(f"SIMULATOR {arm.key}  ({args.episodes} episodes, "
                    f"{Path(args.model).name}, one sample per env step)",
                    _series_stats(sim))
            sim_lengths.append((arm, lengths))

    # HOW LONG IS A FIGHT? Duration is what makes a buff, a control skill or a
    # stress heal worth a turn -- PLAYBOOK 3.5's whole point -- so a simulator
    # whose combats are systematically shorter than the game's would price
    # every one of them too low, and the policy would be RIGHT to skip them.
    print("FIGHT LENGTH (rounds reached per combat)")
    if paths:
        rows = [json.loads(l) for p in paths
                for l in Path(p).read_text(encoding="utf-8",
                                           errors="replace").splitlines()
                if l.strip()]
        real_plays = [r for r in rows if r.get("kind") == "play" and "round" in r]
        records = combat_records(real_plays)
        _rounds_report("REAL PLAY", [c.rounds for c in records], seed=args.seed)
        _by_party_report("REAL PLAY", records, seed=args.seed)
    for arm, lengths in sim_lengths:
        _rounds_report(f"SIMULATOR {arm.key}  ({arm.label})", lengths,
                       seed=args.seed)
    print()

    print("  Every arm carries a bootstrap interval, including the real one --")
    print("  ten combats cannot support a bare mean, and this comparison has")
    print("  been quoted as one. Read the >3 SHARE first: it is what PLAYBOOK")
    print("  3.5's rule turns on and it survives a small sample far better.")
    print()
    print("  Arm A is the baseline the 2.0 came from; B is the same world the")
    print("  real combats were played in (Apprentice, short, crypts/weald,")
    print("  rank 0); C is B cut to two heroes, so B vs C is party size alone.")
    print("  B near 4 means the gap was the TIER MISMATCH and the finding")
    print("  dissolves; B near 2 means the gap is real and is not tier.")
    print()
    print("  For the stress half: does P(recover) agree, and are the two rise")
    print("  rates close enough that 'it accelerates' is not the mechanic?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
