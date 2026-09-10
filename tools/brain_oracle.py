"""Grade the monster-AI weighting against the REAL game.

Sibling of `tools/hit_oracle.py`, and the same trick: `Darkest.exe` already
tells us, over `OutputDebugStringA`, exactly how its own brain weighted a
decision. A skill selection looks like this (Cultist Witch, from
`archive/traces/game_trace.log`):

    MonsterBrain: Select Skill:
    valid skills: cultist_pull, cultist_push, cultist_incantation
    Desire: specific_skill         Skill chances: 0, 1, 0
    Desire: specific_skill         Skill chances: 1, 1, 0
    Desire: specific_skill         Skill chances: 1, 1, 2
    Desire: performing_turn_skill  Skill chances: 1, 1, 7
    MonsterBrain: selected skill at index: 2

One block per desire, **in the brain file's own order**, each printing the
running total. So the *delta* between consecutive blocks is precisely what
that one desire contributed — which makes every desire individually gradeable
against `_select_enemy_skill`, including "a gated desire contributes nothing".

What the corpus cannot supply is the board state (who was marked, who was
stunned, how hurt the monsters were), so a desire that carries a gate is
reported rather than scored: a zero contribution there is equally consistent
with the gate having been closed at the time. Ungated desires must match
exactly.

    python tools/brain_oracle.py
    python tools/brain_oracle.py --verbose
    python tools/brain_oracle.py --emit        # refresh the tracked fixture

Two details of the game's own arithmetic, worth knowing before comparing:

* **Skill weights carry no epsilon** — the numbers above are exactly 0, 1, 2,
  7. Target weights DO: each target desire contributes `base_chance + 0.0001`,
  so a desire that does not apply still nudges by 1e-4. That epsilon only
  matters when every weight would otherwise be zero, where it makes the pick
  uniform — which is what `SeededRNG.weighted_choice` already falls back to.
* `performing_turn_skill` fires on `action_idx + 1 == performing_turn`, which
  the Witch's `+5.0` on her first action confirms.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.store_cache import load_store_cached

from data.paths import CSV_DIR as _CSV_DIR, GAME_DIR as _GAME_DIR

GAME_DIR = str(_GAME_DIR)
CSV_DIR = str(_CSV_DIR)
FIXTURE = Path(CSV_DIR) / "archive" / "traces" / "brain_blocks.json"

RE_DESIRE = re.compile(r"Desire: (\w+)")
RE_SELECTED = re.compile(r"selected skill at index: (\d+)")

# A desire carrying any of these depends on board state the trace does not
# record, so its contribution is observed but not scored.
GATE_FIELDS = (
    "monsters_size_min", "monsters_size_max", "marked_heroes_min",
    "marked_heroes_max", "non_stunned_heroes_min", "hp_ratio_threshold",
    "first_initiative_only", "guarded_monsters_min", "guarded_monsters_max",
    "monsters_min", "monsters_max", "virtued_heroes_max", "heroes_min",
    "non_deaths_door_heroes_min", "non_marked_heroes_min", "performing_turn_min",
    "ally_base_class_id", "effect_key_status",
)


def _body(line: str) -> str:
    return line.split("] ", 1)[-1].strip()


def parse_trace(path: str) -> list[dict]:
    """One record per `MonsterBrain: Select Skill:` block."""
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict] = []
    i = 0
    while i < len(lines):
        if "MonsterBrain: Select Skill:" not in lines[i]:
            i += 1
            continue
        j = i + 1
        if j < len(lines) and _body(lines[j]) == "valid skills:":
            j += 1
        skills: list[str] = []
        while j < len(lines) and "MonsterBrain:" not in lines[j]:
            s = _body(lines[j]).rstrip(",")
            if s:
                skills.append(s)
            j += 1

        steps: list[dict] = []
        selected = None
        while j < len(lines):
            if RE_SELECTED.search(lines[j]):
                selected = int(RE_SELECTED.search(lines[j]).group(1))
                j += 1
                break
            m = RE_DESIRE.search(lines[j])
            if not m:
                if "MonsterBrain: Select Skill:" in lines[j]:
                    break
                j += 1
                continue
            dtype = m.group(1)
            k = j + 1
            while k < len(lines) and "Skill chances" not in lines[k]:
                k += 1
            vec: list[float] = []
            k += 1
            while k < len(lines) and len(vec) < len(skills):
                s = _body(lines[k]).rstrip(",")
                if not s:
                    break
                vec.append(float(s))
                k += 1
            steps.append({"type": dtype, "running": vec})
            j = k
        if skills:
            # A block with no desire at all is the game short-circuiting a
            # single usable skill. Kept, so the corpus count reconciles with
            # a plain grep, but there is nothing in it to grade.
            out.append({"source": os.path.basename(path), "skills": skills,
                        "steps": steps, "selected": selected,
                        "trivial": not steps})
        i = j if j > i else i + 1
    return out


def parse_targets(path: str) -> list[dict]:
    """One record per `MonsterBrain: Select Target` block.

    Same shape as the skill blocks -- one entry per desire, in the brain
    file's order, each with the running total -- except that the target
    vector carries an epsilon: every desire contributes `base_chance + 1e-4`,
    so a desire that does not apply still nudges. That only matters when all
    weights would otherwise be zero, where it makes the pick uniform, which
    is `SeededRNG.weighted_choice`'s own fallback.
    """
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict] = []
    i = 0
    while i < len(lines):
        if "MonsterBrain: Select Target" not in lines[i]:
            i += 1
            continue
        j = i + 1
        steps: list[dict] = []
        while j < len(lines):
            if "MonsterBrain: Select" in lines[j] or "state change" in lines[j]:
                break
            m = RE_DESIRE.search(lines[j])
            if not m:
                j += 1
                continue
            dtype = m.group(1)
            k = j + 1
            valid: list[int] = []
            while k < len(lines) and "Valid Target indexes" not in lines[k]:
                if "Target chances" in lines[k]:
                    break
                k += 1
            if "Valid Target indexes" in lines[k]:
                k += 1
                while k < len(lines) and "Target chances" not in lines[k]:
                    s = _body(lines[k]).rstrip(",")
                    if s:
                        valid.append(int(s))
                    k += 1
            vec: list[float] = []
            k += 1
            while k < len(lines):
                s = _body(lines[k]).rstrip(",")
                if not s or not re.fullmatch(r"-?[0-9.]+", s):
                    break
                vec.append(float(s))
                k += 1
            steps.append({"type": dtype, "valid": valid, "running": vec})
            j = k
        if steps:
            out.append({"source": os.path.basename(path), "steps": steps})
        i = j if j > i else i + 1
    return out


TARGET_EPSILON = 0.0001


def identify(block: dict, store) -> tuple[str, object] | tuple[None, None]:
    """Which brain produced this block?

    Matched on two things at once — the monster owns every skill in the
    `valid skills` list, and its brain's desire TYPES are the logged sequence
    in the logged order. Skills alone are not enough (variants share them) and
    the sequence alone is not either.
    """
    want = [s["type"] for s in block["steps"]]
    seen = set()
    for ed in store.enemies.values():
        brain = store.monster_brains.get(ed.monster_brain_id)
        if not brain or ed.monster_brain_id in seen:
            continue
        own = {sk.id for sk in ed.skills}
        if not set(block["skills"]) <= own:
            continue
        if [d.type for d in brain.skill_desires] != want:
            continue
        seen.add(ed.monster_brain_id)
        return ed.monster_brain_id, brain
    return None, None


def expected_delta(desire, skills: list[str]) -> list[float] | None:
    """What `_select_enemy_skill` gives this one desire, gates assumed open.

    `None` where the engine spreads a weight over every usable skill
    (`random_skill`/`preferred_skill`, and a `heal_skill` with no explicit
    skill id): the game's own spread is not observable from one block, so
    those are reported rather than scored.
    """
    if desire.type in ("random_skill", "preferred_skill"):
        return None
    if desire.type == "heal_skill" and not desire.combat_skill_id:
        return None
    target = desire.combat_skill_id
    if not target:
        return None
    return [desire.base_chance if s == target else 0.0 for s in skills]


def grade(blocks: list[dict], store) -> dict:
    res = {"blocks": len(blocks), "identified": 0, "unidentified": [],
           "scored": 0, "agree": 0, "mismatch": [], "gated": 0, "unscorable": 0,
           "trivial": sum(1 for b in blocks if b.get("trivial"))}
    for b in blocks:
        if b.get("trivial"):
            continue
        brain_id, brain = identify(b, store)
        # Deliberately NOT written back into `b`: the blocks are the tracked
        # fixture, and a grading pass that mutates them makes the "does the
        # fixture still match the raw captures" check fail on its own output.
        res.setdefault("brains", {})[id(b)] = brain_id
        if not brain:
            res["unidentified"].append(b["skills"])
            continue
        res["identified"] += 1
        prev = [0.0] * len(b["skills"])
        for step, desire in zip(b["steps"], brain.skill_desires):
            got = [round(a - c, 6) for a, c in zip(step["running"], prev)]
            prev = step["running"]
            want = expected_delta(desire, b["skills"])
            if want is None:
                res["unscorable"] += 1
                continue
            if any(getattr(desire, f, None) not in (None, False, "")
                   for f in GATE_FIELDS):
                res["gated"] += 1
                continue
            res["scored"] += 1
            if got == [round(w, 6) for w in want]:
                res["agree"] += 1
            else:
                res["mismatch"].append({"source": b["source"], "brain": brain_id,
                                        "desire": desire.type,
                                        "skill": desire.combat_skill_id,
                                        "expected": want, "logged": got})
    return res


def grade_targets(blocks: list[dict]) -> dict:
    """The state-free half of the target side.

    A `random_target` desire weights every valid target the same, whatever
    the board looks like, so its contribution must be exactly
    `base_chance + 1e-4` on each -- and every real one ships at 1.0. That is
    also the desire the engine used to DROP for nine monsters, by filtering
    on `is_friendly_target_desire` alone when 25 desires set both side flags.
    """
    res = {"blocks": len(blocks), "random_desires": 0, "agree": 0,
           "epsilon": set(), "other": 0}
    for b in blocks:
        prev = None
        for step in b["steps"]:
            run = step["running"]
            delta = run if prev is None else [round(a - c, 6) for a, c in zip(run, prev)]
            prev = run
            if step["type"] != "random_target":
                res["other"] += 1
                continue
            res["random_desires"] += 1
            live = [delta[i] for i in step["valid"] if i < len(delta)]
            if live and len(set(round(x, 6) for x in live)) == 1:
                res["agree"] += 1
                res["epsilon"].add(round(live[0] - 1.0, 6))
    res["epsilon"] = sorted(res["epsilon"])
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traces", default=str(Path(CSV_DIR) / "archive" / "traces"))
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    blocks: list[dict] = []
    targets: list[dict] = []
    for f in sorted(glob.glob(os.path.join(args.traces, "*.log"))):
        blocks.extend(parse_trace(f))
        targets.extend(parse_targets(f))
    where = args.traces
    if not blocks and FIXTURE.exists():
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        blocks, targets = payload["skills"], payload["targets"]
        where = str(FIXTURE)
    if not blocks:
        print("no MonsterBrain skill-selection blocks found in %s" % where)
        return 1

    store = load_store_cached(CSV_DIR, GAME_DIR)
    res = grade(blocks, store)

    tres = grade_targets(targets)

    print("corpus: %s" % where)
    print("  skill-selection blocks    %d  (%d trivial: one usable skill, the"
          " game prints no desire)" % (res["blocks"], res["trivial"]))
    print("  brain identified          %d" % res["identified"])
    print("  desire contributions      %d scored, %d gated (board state unknown),"
          " %d spread-weighted"
          % (res["scored"], res["gated"], res["unscorable"]))
    print("  engine agrees             %d / %d  (%.1f%%)"
          % (res["agree"], res["scored"], 100.0 * res["agree"] / max(res["scored"], 1)))
    if res["unidentified"]:
        print("  unidentified skill sets:  %s" % res["unidentified"])
    for m in res["mismatch"]:
        print("    MISMATCH %s %s(%s): engine %s, game %s"
              % (m["brain"], m["desire"], m["skill"], m["expected"], m["logged"]))

    if args.verbose:
        for b in blocks:
            print("\n%s  brain=%s  skills=%s  selected=%s"
                  % (b["source"], b.get("brain"), b["skills"], b["selected"]))
            for s in b["steps"]:
                print("     %-24s %s" % (s["type"], s["running"]))

    print()
    print("  target-selection blocks   %d" % tres["blocks"])
    print("  state-free random_target  %d desires, engine agrees %d (%.1f%%)"
          % (tres["random_desires"], tres["agree"],
             100.0 * tres["agree"] / max(tres["random_desires"], 1)))
    print("  the game's target epsilon %s  (skill weights carry none)"
          % tres["epsilon"])
    print("  state-dependent desires   %d, reported not scored" % tres["other"])

    if args.emit:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps({"skills": blocks, "targets": targets},
                                      indent=1), encoding="utf-8")
        print("\nwrote %s (%d skill blocks, %d target blocks)"
              % (FIXTURE, len(blocks), len(targets)))

    return 0 if not res["mismatch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
