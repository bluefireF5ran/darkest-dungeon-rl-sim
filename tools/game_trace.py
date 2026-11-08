"""Parse a raw `dbwin_capture.py` log into structured combat events.

The capture is a flat line stream mixing several subsystems. This turns it into
typed events so the differential tests can compare the real game against
`CombatEngine` turn by turn instead of eyeballing text.

What the game actually gives us (all verified against real captures):

  * **The whole RNG stream** — `Random uniform: 0.674003 Seed: 1447410098`, plus
    int and ranged-int draws. `uniform == seed / (2**31 - 1)` holds on every
    sample seen, so `seed` is the raw 31-bit generator state. With `-randomseed`
    this makes replay exact rather than merely distributional.
  * **Attack resolution** — `Chance to hit` / `Roll to hit`.
  * **Damage** — `Random int range A,B: 3 , 5 Value: 3`.
  * **Monster AI** — desire type plus the per-skill / per-target chance vectors
    and the chosen index.
  * **Turn structure** — the `bs_*` battle state machine, and
    `check pCurrentBattleActor` naming who is acting.

Usage:
    python tools/game_trace.py game_trace.log --summary
    python tools/game_trace.py game_trace.log --turns
    python tools/game_trace.py game_trace.log --attacks

Note actor names come out in the game's active language (a Spanish install logs
`Cruzado`, not `Crusader`). Capture with `-start_language english` for traces
you intend to match against hero ids.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

RNG_DENOM = 2 ** 31 - 1


# --- events -----------------------------------------------------------------

@dataclass
class Event:
    line_no: int
    raw: str


@dataclass
class RngDraw(Event):
    stream: str          # "Battle" | "Rules" | ...
    kind: str            # "uniform" | "int" | "int_ab" | "get_seed"
    seed: Optional[int] = None
    uniform: Optional[float] = None
    value: Optional[int] = None
    low: Optional[int] = None
    high: Optional[int] = None

    def seed_matches_uniform(self, tol: float = 1e-6) -> Optional[bool]:
        """True when uniform == seed / (2**31 - 1). None when not applicable."""
        if self.uniform is None or self.seed is None:
            return None
        return abs(self.seed / RNG_DENOM - self.uniform) <= tol


@dataclass
class HitCheck(Event):
    chance: float = 0.0
    roll: Optional[float] = None

    @property
    def hit(self) -> Optional[bool]:
        return None if self.roll is None else self.roll < self.chance


@dataclass
class BattleState(Event):
    to_state: str = ""
    from_state: str = ""


@dataclass
class CurrentActor(Event):
    slot: int = -1
    name: str = ""


@dataclass
class InitiativeRoll(Event):
    value: int = 0


@dataclass
class BrainSkillOptions(Event):
    skills: list[str] = field(default_factory=list)


@dataclass
class BrainDesire(Event):
    desire: str = ""
    skill_chances: list[float] = field(default_factory=list)
    target_indexes: list[int] = field(default_factory=list)
    target_chances: list[float] = field(default_factory=list)


@dataclass
class BrainSelectedSkill(Event):
    index: int = 0


@dataclass
class Other(Event):
    pass


# --- parsing ----------------------------------------------------------------

#: `@<unix time> ` is optional: `dbwin_capture.py --stamp` writes it so a trace
#: pairs with the saves of the same session (tools/damage_oracle.py).
_PID_PREFIX = re.compile(r"^(?:@[\d.]+ )?\[(\d+)\]\s?")
_RE_UNIFORM = re.compile(r"Name: (\w+) Random uniform: ([\d.]+) Seed: (\d+)")
_RE_INT_AB = re.compile(r"Name: (\w+) Random int range A,B: (\d+) , (\d+) Value: (\d+) Seed: (\d+)")
_RE_INT = re.compile(r"Name: (\w+) Random int range: (\d+) Value: (\d+) Seed: (\d+)")
_RE_GETSEED = re.compile(r"Name: (\w+) Get seed (\d+)")
_RE_CHANCE = re.compile(r"Chance to hit: ([\d.]+)")
_RE_ROLL = re.compile(r"Roll to hit: ([\d.]+)")
_RE_STATE = re.compile(r"Battle: state change to (\w+), \(from (\w+)\)")
# WARNING: this does NOT name the acting combatant, despite the field name.
# The lines arrive in groups of three AFTER a turn has resolved
# ("#1: Vestal, #2: Bone Rabble, #3: Bone Rabble") — a candidate scan. Do not
# use it to attribute RNG draws to a combatant; --draws' per-actor table is
# unreliable for exactly this reason. Attribute by TURN WINDOW instead
# (bs_turn_do_skill .. bs_after_turn), which is unambiguous, and match the
# pooled bands against what the whole board could roll. See CHANGELOG.md.
_RE_ACTOR = re.compile(r"check pCurrentBattleActor #(\d+): (.+?)\s+\d+\s*$")
_RE_INIT = re.compile(r"RollInitiative (\d+)")
_RE_SELECTED = re.compile(r"MonsterBrain: selected skill at index: (\d+)")
_RE_DESIRE = re.compile(r"^\s*Desire: (\w+)")
_RE_NUM = re.compile(r"^\s*(-?[\d.]+),\s*$")
_RE_WORD = re.compile(r"^\s*([a-zA-Z_][\w]*),\s*$")


def _strip_pid(line: str) -> str:
    return _PID_PREFIX.sub("", line.rstrip("\n").rstrip())


def parse(lines: Iterable[str]) -> list[Event]:
    """Line-driven parse. The MonsterBrain output spans several
    OutputDebugString calls (header, label, then one value per line, then a
    blank), so those need accumulating rather than matching line-wise."""
    out: list[Event] = []
    raw_lines = [(i + 1, _strip_pid(l)) for i, l in enumerate(lines)]

    i = 0
    n = len(raw_lines)
    pending_chance: Optional[HitCheck] = None

    while i < n:
        line_no, text = raw_lines[i]
        i += 1
        if not text:
            continue

        m = _RE_UNIFORM.search(text)
        if m:
            out.append(RngDraw(line_no, text, stream=m.group(1), kind="uniform",
                               uniform=float(m.group(2)), seed=int(m.group(3))))
            continue
        m = _RE_INT_AB.search(text)
        if m:
            out.append(RngDraw(line_no, text, stream=m.group(1), kind="int_ab",
                               low=int(m.group(2)), high=int(m.group(3)),
                               value=int(m.group(4)), seed=int(m.group(5))))
            continue
        m = _RE_INT.search(text)
        if m:
            out.append(RngDraw(line_no, text, stream=m.group(1), kind="int",
                               high=int(m.group(2)), value=int(m.group(3)),
                               seed=int(m.group(4))))
            continue
        m = _RE_GETSEED.search(text)
        if m:
            out.append(RngDraw(line_no, text, stream=m.group(1), kind="get_seed",
                               seed=int(m.group(2))))
            continue

        m = _RE_CHANCE.search(text)
        if m:
            # "Roll to hit" always follows on the next line; pair them so a
            # hit/miss verdict is a property of one event.
            pending_chance = HitCheck(line_no, text, chance=float(m.group(1)))
            out.append(pending_chance)
            continue
        m = _RE_ROLL.search(text)
        if m:
            if pending_chance is not None:
                pending_chance.roll = float(m.group(1))
                pending_chance = None
            else:
                out.append(HitCheck(line_no, text, chance=float("nan"),
                                    roll=float(m.group(1))))
            continue

        m = _RE_STATE.search(text)
        if m:
            out.append(BattleState(line_no, text, to_state=m.group(1), from_state=m.group(2)))
            continue
        m = _RE_ACTOR.search(text)
        if m:
            out.append(CurrentActor(line_no, text, slot=int(m.group(1)), name=m.group(2).strip()))
            continue
        m = _RE_INIT.search(text)
        if m:
            out.append(InitiativeRoll(line_no, text, value=int(m.group(1))))
            continue
        m = _RE_SELECTED.search(text)
        if m:
            out.append(BrainSelectedSkill(line_no, text, index=int(m.group(1))))
            continue

        if text.startswith("MonsterBrain: Select Skill"):
            skills, i = _collect_words(raw_lines, i, header="valid skills:")
            out.append(BrainSkillOptions(line_no, text, skills=skills))
            continue

        if text.rstrip() == "MonsterBrain:":
            desire, i = _collect_desire(raw_lines, i, line_no)
            if desire:
                out.append(desire)
            continue

        out.append(Other(line_no, text))

    return out


def _collect_words(raw_lines, i, header: str) -> tuple[list[str], int]:
    n = len(raw_lines)
    words: list[str] = []
    if i < n and raw_lines[i][1].strip().startswith(header):
        i += 1
    while i < n:
        text = raw_lines[i][1]
        if not text.strip():
            i += 1
            break
        m = _RE_WORD.match(text)
        if not m:
            break
        words.append(m.group(1))
        i += 1
    return words, i


def _collect_desire(raw_lines, i, line_no) -> tuple[Optional[BrainDesire], int]:
    """A desire block:  Desire: <type> / [ Valid Target indexes: <ints>] /
    ( Skill chances: | Target chances: ) <floats> / blank."""
    n = len(raw_lines)
    if i >= n:
        return None, i
    m = _RE_DESIRE.match(raw_lines[i][1])
    if not m:
        return None, i
    ev = BrainDesire(line_no, raw_lines[i][1], desire=m.group(1))
    i += 1

    bucket: Optional[list] = None
    as_int = False
    while i < n:
        text = raw_lines[i][1]
        stripped = text.strip()
        if not stripped:
            i += 1
            break
        if stripped.startswith("Valid Target indexes:"):
            bucket, as_int = ev.target_indexes, True
            i += 1
            continue
        if stripped.startswith("Target chances:"):
            bucket, as_int = ev.target_chances, False
            i += 1
            continue
        if stripped.startswith("Skill chances:"):
            bucket, as_int = ev.skill_chances, False
            i += 1
            continue
        m2 = _RE_NUM.match(text)
        if m2 and bucket is not None:
            bucket.append(int(float(m2.group(1))) if as_int else float(m2.group(1)))
            i += 1
            continue
        break
    return ev, i


def parse_file(path: str | Path) -> list[Event]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse(f)


# --- turn grouping ----------------------------------------------------------

@dataclass
class Turn:
    index: int
    actor: Optional[str] = None
    events: list[Event] = field(default_factory=list)

    @property
    def hit_checks(self) -> list[HitCheck]:
        return [e for e in self.events if isinstance(e, HitCheck)]

    @property
    def damage_rolls(self) -> list[RngDraw]:
        return [e for e in self.events if isinstance(e, RngDraw) and e.kind == "int_ab"]

    @property
    def states(self) -> list[str]:
        return [e.to_state for e in self.events if isinstance(e, BattleState)]


def group_attacks(events: list[Event]) -> list[list[HitCheck]]:
    """Group hit checks belonging to one skill use.

    One `bs_turn_presentation` entry is one skill resolving, so every hit check
    between two such transitions belongs to the same attack — that is what makes
    "same attack, several targets" distinguishable from "several attacks".
    """
    groups: list[list[HitCheck]] = []
    cur: list[HitCheck] = []
    for e in events:
        if isinstance(e, BattleState) and e.to_state in ("bs_turn_presentation",
                                                          "bs_before_turn_start"):
            if cur:
                groups.append(cur)
                cur = []
        if isinstance(e, HitCheck):
            cur.append(e)
    if cur:
        groups.append(cur)
    return groups


def group_turns(events: list[Event], boundary: str = "bs_before_turn_start") -> list[Turn]:
    """Split the stream into turns at a battle-state boundary."""
    turns: list[Turn] = []
    cur: Optional[Turn] = None
    for e in events:
        if isinstance(e, BattleState) and e.to_state == boundary:
            cur = Turn(index=len(turns))
            turns.append(cur)
        if cur is None:
            cur = Turn(index=0)
            turns.append(cur)
        cur.events.append(e)
        if isinstance(e, CurrentActor) and cur.actor is None and e.name != "nullptr":
            cur.actor = e.name
    return turns


# --- CLI --------------------------------------------------------------------

def prng_advances(events: list[Event]) -> list[RngDraw]:
    """The real PRNG advances, de-duplicated by seed.

    **Counting `Random ...` lines overcounts draws by up to 3x.** The game
    logs one draw at every layer it passes through, all carrying the SAME
    seed — the raw uniform, then the bare int conversion, then the A,B
    conversion:

        Random uniform: 0.8884044886      Seed: 1907834115
        Random int range: 3   Value: 2    Seed: 1907834115
        Random int range A,B: 2 , 4 Value: 4  Seed: 1907834115

    That is ONE advance reported three ways, not three draws. Both
    conversions are derived from the same uniform:

        int range N     -> floor(u * N)              floor(0.8884*3) = 2
        int range A,B   -> A + floor(u * (B-A+1))    2 + floor(0.8884*3) = 4

    (so A,B is INCLUSIVE of both ends). Any attempt to align the game's
    stream against a simulator's must dedupe first or it desynchronises
    immediately.
    """
    out: list[RngDraw] = []
    last_seed = None
    for e in events:
        if isinstance(e, RngDraw):
            if e.seed is not None and e.seed == last_seed:
                continue
            out.append(e)
            last_seed = e.seed
        else:
            last_seed = None
    return out


def check_int_range_formula(events: list[Event]) -> tuple[int, int, list[str]]:
    """Verify A + floor(u*(B-A+1)) against every A,B draw that shares its
    seed with a logged uniform. Returns (ok, total, failures)."""
    by_seed: dict[int, float] = {}
    ok = total = 0
    fails: list[str] = []
    for e in events:
        if not isinstance(e, RngDraw):
            continue
        if e.kind == "uniform" and e.seed is not None and e.uniform is not None:
            by_seed[e.seed] = e.uniform
        elif e.kind == "int_ab" and e.seed in by_seed and None not in (e.low, e.high, e.value):
            u = by_seed[e.seed]
            expected = e.low + int(u * (e.high - e.low + 1))
            total += 1
            if expected == e.value:
                ok += 1
            else:
                fails.append(f"line {e.line_no}: u={u:.9f} range={e.low},{e.high} "
                             f"expected {expected} got {e.value}")
    return ok, total, fails


def skill_execution_shapes(events: list[Event]) -> list[list[Event]]:
    """One list of draws/hit-checks per skill execution, bracketed by
    bs_turn_do_skill .. bs_after_turn, with draws already deduped."""
    seq = prng_advances(events)
    keep = [e for e in events if isinstance(e, (BattleState, HitCheck))]
    merged = sorted(seq + keep, key=lambda e: e.line_no)
    runs: list[list[Event]] = []
    cur: Optional[list[Event]] = None
    for e in merged:
        if isinstance(e, BattleState):
            if e.to_state == "bs_turn_do_skill":
                cur = []
            elif e.to_state == "bs_after_turn" and cur is not None:
                runs.append(cur)
                cur = None
        elif cur is not None:
            cur.append(e)
    return runs


def damage_ranges_by_actor(events: list[Event]) -> dict[str, "collections.Counter"]:
    """Every (A,B) damage range the game actually rolled, keyed by whichever
    combatant the trace last named as the current actor. These are ground
    truth for the simulator's own damage-range arithmetic."""
    import collections
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    actor = "?"
    for e in sorted(events, key=lambda e: e.line_no):
        if isinstance(e, CurrentActor):
            actor = e.name
        elif isinstance(e, RngDraw) and e.kind == "int_ab" and e.low is not None:
            out[actor][(e.low, e.high)] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace", help="log written by tools/dbwin_capture.py --out")
    ap.add_argument("--summary", action="store_true", help="event-type counts and RNG integrity")
    ap.add_argument("--turns", action="store_true", help="one line per turn")
    ap.add_argument("--attacks", action="store_true", help="every hit check with its damage roll")
    ap.add_argument("--states", action="store_true", help="observed battle state-machine order")
    ap.add_argument("--aoe", action="store_true",
                    help="do multi-target attacks share one to-hit roll? (fidelity check)")
    ap.add_argument("--draws", action="store_true",
                    help="PRNG advances per skill execution + real damage ranges per actor")
    args = ap.parse_args()

    events = parse_file(args.trace)
    if not any((args.summary, args.turns, args.attacks, args.states, args.aoe, args.draws)):
        args.summary = True

    if args.draws:
        import collections
        raw = [e for e in events if isinstance(e, RngDraw)]
        adv = prng_advances(events)
        print(f"{len(raw)} logged RNG lines -> {len(adv)} real PRNG advances "
              f"({len(raw) - len(adv)} were the same seed re-reported at another layer)\n")

        ok, total, fails = check_int_range_formula(events)
        print(f"int-range formula  A + floor(u*(B-A+1))  [A,B inclusive]: {ok}/{total} verified")
        for f in fails[:5]:
            print(f"    MISMATCH {f}")
        print()

        runs = skill_execution_shapes(events)
        shapes = collections.Counter()
        for r in runs:
            shapes["".join("c" if isinstance(e, HitCheck) else "U" for e in r)] += 1
        print(f"draw shape per skill execution ({len(runs)} bracketed by do_skill..after_turn)")
        print("  U = one PRNG advance, c = one logged Chance/Roll-to-hit pair")
        for shape, n in shapes.most_common(8):
            print(f"    n={n:3d}  {shape}")
        print("\n  The to-hit roll is the FIRST advance of each execution and is only")
        print("  reported after two further advances, so a simulator that draws lazily")
        print("  (hit, then crit only if it hit) cannot be aligned draw-for-draw.\n")

        print("real damage ranges rolled, by acting combatant:")
        for actor, c in sorted(damage_ranges_by_actor(events).items()):
            if actor in ("?", "nullptr"):
                continue
            print(f"  {actor:20s} " + ", ".join(f"{lo}-{hi}(x{n})" for (lo, hi), n in sorted(c.items())))

    if args.aoe:
        groups = group_attacks(events)
        multi = [g for g in groups if len(g) > 1]
        print(f"{len(groups)} attacks, {len(multi)} of them multi-target\n")
        shared = distinct = 0
        by_size: dict[int, list[int]] = {}
        for g in multi:
            rolls = {h.roll for h in g}
            chances = {h.chance for h in g}
            is_shared = len(rolls) == 1
            shared += is_shared
            distinct += not is_shared
            by_size.setdefault(len(g), [0, 0])[0 if is_shared else 1] += 1
            print(f"  {len(g)} targets | distinct rolls={len(rolls)} chances={len(chances)}"
                  f" -> {'SHARED' if is_shared else 'DISTINCT'}"
                  f"   roll={sorted(r for r in rolls if r is not None)}"
                  f" chance={sorted(chances)}")
        print(f"\n  shared-roll attacks:   {shared}")
        print(f"  distinct-roll attacks: {distinct}   <- counterexamples")
        print("\n  by target count:")
        for k in sorted(by_size):
            s, d = by_size[k]
            print(f"    {k} targets: {s} shared, {d} distinct")
        if multi and distinct == 0:
            print("\n  VERDICT: the game rolls to-hit ONCE per multi-target attack and")
            print("  compares it against each target's own chance. A simulator that rolls")
            print("  independently per target reproduces the mean but not the variance.")
        elif not multi:
            print("\n  No multi-target attack captured — use a wide AoE (Houndmaster's")
            print("  Hound's Harry, ranks 1-4; Highwayman's Grapeshot Blast, ranks 1-3)")
            print("  against 3-4 living enemies.")

    if args.summary:
        import collections
        c = collections.Counter(type(e).__name__ for e in events)
        print(f"{len(events)} events from {args.trace}")
        for k, v in c.most_common():
            print(f"  {v:6d}  {k}")

        draws = [e for e in events if isinstance(e, RngDraw) and e.uniform is not None]
        ok = sum(1 for d in draws if d.seed_matches_uniform())
        print(f"\nRNG integrity: {ok}/{len(draws)} draws satisfy uniform == seed / (2**31-1)")
        streams = collections.Counter(e.stream for e in events if isinstance(e, RngDraw))
        print(f"RNG streams: {dict(streams)}")

    if args.states:
        seen, order = set(), []
        for e in events:
            if isinstance(e, BattleState) and e.to_state not in seen:
                seen.add(e.to_state)
                order.append(f"{e.from_state} -> {e.to_state}")
        print("\nBattle state transitions (first occurrence order):")
        for s in order:
            print("   ", s)

    if args.turns:
        turns = group_turns(events)
        print(f"\n{len(turns)} turns")
        for t in turns:
            hits = t.hit_checks
            landed = sum(1 for h in hits if h.hit)
            print(f"  turn {t.index:3d}  actor={t.actor or '?':24s} "
                  f"events={len(t.events):4d}  hit_checks={len(hits)} (landed {landed})  "
                  f"dmg_rolls={len(t.damage_rolls)}")

    if args.attacks:
        print("\nHit checks:")
        for e in events:
            if isinstance(e, HitCheck):
                verdict = "?" if e.hit is None else ("HIT " if e.hit else "MISS")
                roll = "?" if e.roll is None else f"{e.roll:.6f}"
                print(f"  line {e.line_no:5d}  chance={e.chance:.6f} roll={roll} -> {verdict}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
