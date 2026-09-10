"""Can the model SEE this class? — the gate a modded class must pass.

Fran's standing goal is a policy that plays any class, vanilla or modded, and
the route to it is training data rather than architecture: four model-side
transfer mechanisms came back null, while three composed comps took the Sibyl
from a -28% swap cost to -1%. So the corpus is the lever.

**But a class can load, resolve every effect id it names, and still be
invisible.** The policy reads a skill through the 45-float payload vector in
`env/gym_env.py::_mechanics_from_data`. A skill whose payload is all zeros is,
to the policy, a bare attack with no mechanic on it. Measured over the 48
Workshop classes that load:

| | skills with an all-zero payload |
|---|---|
| vanilla | 12 of 140 (9%) |
| modded | 126 of 348 (**36%**) |

and five classes are **100% invisible** -- Kuuga 14/14, Mordekaiser 10/10,
forlorn 7/7, duchess 7/7, EosOceanDragonMaid 7/7.

**Training on those would be worse than omitting them.** The policy would be
rewarded for outcomes it cannot attribute to anything it can perceive, and --
because the whole point of the corpus is to test whether pool SCALE buys the
transfer that four model-side mechanisms could not -- it would poison the one
experiment the corpus exists to run.

## Reading the output

`blind` is the count that decides. Some of it is legitimate: a plain
damage-only skill genuinely has no payload, which is what vanilla's 9% is. The
gate therefore admits a class at the vanilla rate and refuses one that is
mostly or wholly unreadable.

`unresolved` is a different failure -- an effect the skill NAMES and the store
does not hold, so that part of the skill does nothing at all. It is judged as a
SHARE of the class's references rather than as a count, and the threshold sits
in a gap in the measured distribution rather than being chosen:

| class | unresolved | what they are |
|---|---|---|
| `aesthete` | 1 of 34 (2.9%) | `AesESTooltipEffect1D` -- a tooltip |
| `hedgeknight` | 1 of 17 (5.9%) | one id |
| `fermint_rogue` | 2 of 27 (7.4%) | `fr_aim_CD_tt_skill`, `fr_tri_coin_fake_daze` |
| `veiled` | 8 of 57 (14.0%) | real effects |
| `forlorn` | 1 of 7 (14.3%) | ONE id -- see the caveat below |
| `millap_ranni` | 8 of 42 (19.1%) | real effects |
| `Mordekaiser` | 31 of 55 (56.4%) | half its kit is undefined |
| `snor_nurse` | 27 of 27 (100%) | every reference is missing |

**Nothing falls between 7.4% and 14.0%**, so the cut is on a gap and not on a
slope. A mod naming a display string it never defined is not a mod the policy
cannot read -- `fermint_rogue` has SEVEN of seven skills visible. The ones
above the cut are missing the mechanics themselves.

**The gap has moved once already and will move again.** It was 7.4%-17.2%
before the record merge changed the denominators, and the numbers here are a
property of whichever mods are subscribed. Re-read this table before trusting
the constant; do not treat either bound as fixed.

**A share punishes a small denominator, and `forlorn` is the case to know.**
Its 14.3% is ONE missing id out of seven references -- a typo, not broken
data. A count floor would express that better, and is deliberately NOT added:
it would change nothing today (forlorn fails the visibility rule at 2 of 7
regardless), so adding it would be fitting the threshold to the data rather
than to a reason.

Counting is at each skill's MAX level only. The ladder repeats an effect once
per rank, so counting all five inflates the denominator ~5x and makes the same
class look cleaner than it is.

`unreachable_skills` is the third way to be invisible and the least obvious:
a class with more than `MAX_SKILL_SLOTS` skills has no action index for the
extras, so they can never be chosen however well the observation describes
them. Four of the fifty ship more than seven.

## Why this is a ruler

It grades DATA rather than the policy, but it shares the rulers' rule for the
same reason: it reads `_mechanics_from_data`, so anything in `env/` importing
it back would let the observation's own definition of "visible" be shaped by
the gate that measures it. One direction only, and
`tests/suite/test_rulers.py` enforces it.

```bash
python tools/class_visibility.py                      # every loaded class
python tools/class_visibility.py --mods "<dir>;<dir>" # explicit roots
python tools/class_visibility.py --failing            # only what the gate refuses
python tools/class_visibility.py --json out.json      # for the corpus builder
```
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.data_loader import DataLoader          # noqa: E402
from data.mod_roots import MOD_DIRS_ENV          # noqa: E402
from env.gym_env import (CSV_DIR, GAME_DIR, MAX_SKILL_SLOTS,  # noqa: E402
                          DarkestDungeonEnv)

# A class passes when at least this many of its skills carry a payload the
# observation can read. Set against the vanilla baseline rather than chosen:
# 9% of vanilla hero skills are payload-free plain attacks, so a class is not
# suspect for having one or two.
MIN_VISIBLE_SKILLS = 5

# ...and no more than this share of its effect references may fail to resolve.
# A SHARE, not a count, and the value sits in a measured gap: the classes whose
# only unresolved ids are tooltips read 2.9% and 7.4%, the ones actually
# missing mechanics read 17.2%, 52.4% and 100%. See the table in the module
# docstring -- nothing at all falls between 7.4% and 17.2%.
MAX_UNRESOLVED_SHARE = 0.10

# ...and it must fit the action space. `MAX_SKILL_SLOTS` is 7 and its comment
# says "every real hero class has exactly 7 skills", which the Workshop makes
# false: Kuuga ships 14, mtgh 11, Mordekaiser 10, deovolente 9. Actions 0..27
# are `slot(7) * rank(4)`, so a 9th skill has no index and is unreachable --
# invisible by a different route than an empty payload, and refused for the
# same reason. `tests/data/test_mod_heroes.py` is what surfaced this.
MAX_SKILLS = MAX_SKILL_SLOTS

# The twenty the simulator ships with. Used only to split the report; the gate
# is applied identically to every class, which is what makes the vanilla rows
# a control on the threshold rather than an exemption from it.
VANILLA = frozenset({
    "crusader", "vestal", "highwayman", "plague_doctor", "occultist",
    "hellion", "leper", "jester", "grave_robber", "man_at_arms",
    "houndmaster", "arbalest", "bounty_hunter", "abomination", "antiquarian",
    "musketeer", "flagellant", "shieldbreaker", "duelist", "runaway",
})


def _effect_refs(level):
    """Every effect name one skill level names, across all three carriers.

    `.mode_effects` matters here and is easy to miss: a stance class keeps most
    of its kit behind it, and the Sibyl's three `switch_mode_*_self` effects
    are the whole class.
    """
    names = [e for e in (level.effects or []) if e]
    names += [e for e in (level.self_effects or []) if e]
    for mode_list in (level.mode_effects or {}).values():
        names += [e for e in (mode_list or []) if e]
    return names


def audit(store, env):
    """One row per hero class the store holds."""
    rows = []
    for hero_id, hero in sorted(store.heroes.items()):
        total = blind = 0
        refs = 0
        missing = set()
        for skill in hero.skills:
            if not skill.levels:
                continue
            level = skill.levels[max(skill.levels)]
            total += 1
            # The payload the POLICY sees -- taken from the env, never
            # re-derived, or the gate and the observation drift apart.
            if not any(abs(x) > 1e-9 for x in env._mechanics_from_data(level)):
                blind += 1
            for name in _effect_refs(level):
                refs += 1
                if name not in store.effects:
                    missing.add(name)
        if not total:
            continue
        visible = total - blind
        share = (len(missing) / refs) if refs else 0.0
        overflow = max(0, total - MAX_SKILLS)
        rows.append({
            "hero": hero_id,
            "vanilla": hero_id in VANILLA,
            "skills": total,
            "visible": visible,
            "blind": blind,
            "refs": refs,
            "unresolved": len(missing),
            "unresolved_share": round(share, 4),
            "unreachable_skills": overflow,
            "missing_sample": sorted(missing)[:5],
            "passes": (visible >= MIN_VISIBLE_SKILLS
                       and share <= MAX_UNRESOLVED_SHARE
                       and overflow == 0),
        })
    return rows


def load_store(mods: str | None):
    """Build the store, optionally overriding `DDSIM_MOD_DIRS` for this run."""
    if mods:
        os.environ[MOD_DIRS_ENV] = mods
    # The loader is chatty and the report is the product here.
    with contextlib.redirect_stdout(io.StringIO()):
        store = DataLoader.load_all(CSV_DIR, GAME_DIR)
        env = DarkestDungeonEnv(store=store)
    return store, env


def report(rows, failing_only=False):
    shown = [r for r in rows if not failing_only or not r["passes"]]
    shown.sort(key=lambda r: (r["passes"], r["visible"] / r["skills"], r["hero"]))

    print(f"{'class':<26}{'skills':>7}{'visible':>9}{'blind':>7}"
          f"{'refs':>7}{'unres':>7}{'unres%':>8}  verdict")
    for r in shown:
        tag = "" if r["passes"] else "  <-- REFUSED"
        origin = "vanilla" if r["vanilla"] else ""
        print(f"{r['hero']:<26}{r['skills']:>7}{r['visible']:>9}{r['blind']:>7}"
              f"{r['refs']:>7}{r['unresolved']:>7}"
              f"{100 * r['unresolved_share']:>7.1f}%  "
              f"{'PASS' if r['passes'] else 'FAIL'} {origin}{tag}")
        if r["unreachable_skills"]:
            print(f"{'':<26}  {r['unreachable_skills']} skill(s) past slot "
                  f"{MAX_SKILLS} have no action index at all")
        if r["missing_sample"]:
            print(f"{'':<26}  unresolved e.g. {', '.join(r['missing_sample'])}")

    for label, subset in (("vanilla", [r for r in rows if r["vanilla"]]),
                          ("modded", [r for r in rows if not r["vanilla"]])):
        if not subset:
            continue
        skills = sum(r["skills"] for r in subset)
        blind = sum(r["blind"] for r in subset)
        passed = sum(1 for r in subset if r["passes"])
        print(f"\n{label:<8} {len(subset):>3} classes   "
              f"blind skills {blind:>4} of {skills:<4} "
              f"({100 * blind / skills:.0f}%)   "
              f"gate PASS {passed}/{len(subset)}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mods", default=None,
                    help=f"os.pathsep-separated content roots; overrides "
                         f"{MOD_DIRS_ENV} for this run")
    ap.add_argument("--failing", action="store_true",
                    help="print only the classes the gate refuses")
    ap.add_argument("--json", dest="json_out", default=None, metavar="PATH",
                    help="write the full table for the corpus builder to read")
    args = ap.parse_args()

    store, env = load_store(args.mods)
    rows = audit(store, env)
    report(rows, failing_only=args.failing)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"min_visible_skills": MIN_VISIBLE_SKILLS,
                        "max_unresolved_share": MAX_UNRESOLVED_SHARE,
                        "max_skills": MAX_SKILLS,
                        "classes": rows}, indent=2),
            encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
