"""The team-play ruler, run on REAL PLAY instead of on the simulator.

`tools/team_probe.py` measures target selection by playing episodes, and its
findings carry one caveat that it cannot remove from the inside: the party is
curated and the tier is fixed, while Fran's impression came from driving the
bridge through his own campaign with, in his words, *"comps that i'm using
[that] are super low level or with sub-optimized loadouts"*. Two different
distributions.

`bat/combat_play.bat` writes `bat/play.log`, and that log turns out to carry
everything the ruler needs, per decision:

    [20:58:03] [R2] Plague Doctor (rank 3, 22/22 hp, 17 stress)
      2 enemies left, stall 0/4 (0/2 real actions this round)
      -> INCISION on rank 2 (Cultist Acolyte, 5 hp)   79%
         alt: EMBOLDENING VAPOURS on rank 2 (Occultist)  14%
      [act] [battle] Plague Doctor: INCISION on rank 2 (Cultist Acolyte, 5 hp)
    [20:58:08]   MATCH  played attack Cultist Acolyte (slot 2) -  (-4 hp)

The round, the actor, the chosen skill, the target and **its current HP**, and
the runners-up with theirs. So the same three metrics can be computed on the
dungeons Fran actually played, with no simulator anywhere -- the same virtue
`tools/agreement.py` has.

## The one limitation, stated up front

**The alternatives are the policy's own shortlist, not the legal set.** The log
prints the top few, so "reachable" here means "reachable and highly ranked",
and the uniform baseline is over that shortlist. That makes the baseline
*weaker* than `team_probe`'s -- it is a comparison against the policy's own
top choices rather than against everything it could have done -- so a gap
measured here is a conservative version of the same claim.

**Only EXECUTED decisions are graded.** A refused turn (a provisional
look-ahead, pending enemy turns) never happened on the board, so counting its
pick would fabricate an attack that was never made.

Like every other probe here, this is a **RULER**: nothing it measures may
become a reward term, an action mask or a training target.

    python tools/play_log_probe.py                    # bat/play.log
    python tools/play_log_probe.py --log <file> ...   # several, pooled
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HEAD = re.compile(r"^\[\d\d:\d\d:\d\d\] \[R(\d+)\] (?:after .*?-> )?(.+?) \(rank (\d+), ")
# **The target capture stops at the FIRST `)`, and that is load-bearing.** An
# AoE line reads `on rank 2 (Brigand Fusilier, 20 hp) (AoE)  27%`, and a lazy
# `\((.+?)\)` anchored on the percentage swallows `) (AoE` into the name -- so
# the HP no longer parses, the entry reads as ally-directed, and **every AoE
# alternative silently vanishes from the reachable set**. Caught by a fixture,
# not by the real log, which is exactly why the fixture has an AoE line in it.
PICK = re.compile(r"^  -> (.+?) on (?:rank \d+ \(([^)]*)\)|self).*?(\d+)%")
ALT = re.compile(r"^     alt: (.+?) on (?:rank \d+ \(([^)]*)\)|self).*?(\d+)%")
SENT = re.compile(r"^  \[act\] \[battle\] (?!REFUSE)")
REFUSED = re.compile(r"^  \[act\] \[battle\] REFUSE")
ENEMY = re.compile(r"^(.*?), (\d+) hp$")

# An enemy at or under this share of the round's healthiest reachable target is
# "hurt". Expressed RELATIVELY because the log carries an enemy's current HP
# and not its maximum -- the absolute comparison ("is a weaker one in reach")
# is the one the data supports, and it is the one the question is about.
HURT_MARGIN = 0.60


def _target(blob):
    """`(name, hp)` for an enemy, `(name, None)` for an ally or self.

    The log distinguishes them by carrying HP only for an enemy, which is what
    makes an ally-directed skill impossible to mistake for an attack.
    """
    if not blob:
        return None
    m = ENEMY.match(blob)
    return (m.group(1), int(m.group(2))) if m else (blob, None)


def parse(path: Path) -> list[dict]:
    """Every graded decision in one play log, in order."""
    out: list[dict] = []
    cur = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        head = HEAD.match(line)
        if head:
            if cur is not None and cur.get("pick") and cur.get("sent"):
                out.append(cur)
            cur = {"round": int(head.group(1)), "hero": head.group(2),
                   "alts": [], "sent": False, "refused": False}
            continue
        if cur is None:
            continue
        pick = PICK.match(line)
        if pick:
            cur["pick"] = (pick.group(1), _target(pick.group(2)))
            continue
        alt = ALT.match(line)
        if alt:
            cur["alts"].append((alt.group(1), _target(alt.group(2))))
            continue
        if REFUSED.match(line):
            cur["refused"] = True
        elif SENT.match(line):
            cur["sent"] = True
    if cur is not None and cur.get("pick") and cur.get("sent"):
        out.append(cur)
    return out


def _enemy_options(entry):
    """`{name: hp}` for every enemy the shortlist could have hit, chosen one
    included. A name is the key because the log has no combatant id."""
    out = {}
    for _skill, tgt in [entry["pick"]] + entry["alts"]:
        if tgt and tgt[1] is not None:
            out.setdefault(tgt[0], tgt[1])
    return out


def grade(entries, control_names) -> Counter:
    counts: Counter = Counter()
    expected = Counter()
    hit_this_round: set = set()
    last_round = None

    for entry in entries:
        skill, tgt = entry["pick"]
        # **The window is one ROUND, and several heroes act inside one.** The
        # first version reset whenever `round <= last_round`, which is true for
        # every hero after the first in the same round -- so the set was always
        # empty and FOCUS silently graded nothing at all. A round number that
        # goes DOWN is a new combat; one that goes UP is the next round; one
        # that repeats is the next hero, and that is the case being measured.
        if last_round is not None and entry["round"] != last_round:
            hit_this_round = set()
        last_round = entry["round"]

        # -- CONTROL: taken vs its share of the shortlist -------------------
        shortlist = [skill] + [s for s, _t in entry["alts"]]
        control = [s for s in shortlist if s.upper() in control_names]
        if control:
            counts["control_offered"] += 1
            expected["control"] += len(control) / len(shortlist)
            if skill.upper() in control_names:
                counts["control_taken"] += 1

        if not tgt or tgt[1] is None:
            continue                       # an ally- or self-directed turn
        options = _enemy_options(entry)
        name, hp = tgt

        # -- FOCUS: does a follow-up attack repeat this round's target? -----
        if hit_this_round and len(options) >= 2:
            already = hit_this_round & set(options)
            counts["graded"] += 1
            expected["repeat"] += len(already) / len(options)
            if name in hit_this_round:
                counts["repeated"] += 1

        # -- FINISH: was a weaker enemy in the shortlist passed over? -------
        if len(options) >= 2:
            weakest = min(options.values())
            hurt = {n: h for n, h in options.items()
                    if h <= max(HURT_MARGIN * max(options.values()), weakest)}
            if hurt and len(hurt) < len(options):
                counts["choices_with_hurt"] += 1
                passes = sum(1 for n, h in options.items()
                             if n not in hurt and any(x < h for x in hurt.values()))
                expected["left"] += passes / len(options)
                if name not in hurt and any(x < hp for x in hurt.values()):
                    counts["left_standing"] += 1

        hit_this_round.add(name)

    counts["expected_repeat"] = expected["repeat"]
    counts["expected_left"] = expected["left"]
    counts["expected_control"] = expected["control"]
    return counts


def control_display_names(store) -> set:
    """Display names, upper-cased, of every skill the probe calls control.

    Resolved through the same localization the log's own headlines come from,
    so a name printed in the log matches a name here or matches nothing --
    there is no fuzzy middle to get quietly wrong.
    """
    from data.comp_loadout_loader import CompLoadoutLoader
    from env.gym_env import GAME_DIR
    from tools.style_probe import classify

    names = set()
    maps = CompLoadoutLoader._build_skill_name_maps(Path(GAME_DIR),
                                                    set(store.heroes))
    for hero_id, table in maps.items():
        hero = store.heroes.get(hero_id)
        tier = len(getattr(hero, "weapon_tiers", ()) or ()) - 1
        for display, skill_id in table.items():
            data = None
            for level in (max(tier, 0), 0):
                data = store.skill_index().get((skill_id, level))
                if data is not None:
                    break
            if data is not None and classify(data, skill_id, store) == "control":
                names.add(display.upper())
    return names


# -- what real play looks like, against what the simulator samples ---------
#
# Fran: "We did fine tune the model and got really low stress even on champion
# dungeons, what is happening that on real play its much higher than i
# expected?"
#
# The answer is a DISTRIBUTION, not a bug in the stress model -- the darkness
# stress terms are wired (`combat_engine.py` sets `_stress_chance_bonus` and
# `_stress_damage_mult` from the band). What differs is which bands the policy
# ever sees.

TORCH_LINE = re.compile(r"torch (\d+) \(")
TIER_LINE = re.compile(r"combats left in (\w+) \(tier (\d+)\)")
# **`re.MULTILINE`, and it is not decoration.** This one is `findall` over the
# whole file rather than `match` per line, and without the flag `^` anchors to
# the START OF THE STRING -- so it matched exactly once and the stress summary
# read "n=1, max 0" on a log with 378 of them.
STRESS_LINE = re.compile(
    r"^\[\d\d:\d\d:\d\d\] \[R\d+\] (?:after .*?-> )?(.+?) "
    r"\(rank \d+, (\d+)/(\d+) hp, (\d+) stress\)", re.MULTILINE)

# `rules.json`'s own darkness table, as the ranges it ships. `lowest_excluded`
# is true, so a band EXCLUDES its lower bound -- torch 75 is dim, not radiant.
# The stress columns are the point: they are what makes this question about
# torch at all.
BANDS = ((75.0, "radiant", 0, 0), (50.0, "dim", 5, 10), (25.0, "dark", 10, 20),
         (0.0, "black", 25, 30), (float("-inf"), "pitch black", 35, 40))


def band_of(torch: float) -> str:
    for lower, name, _sc, _sd in BANDS:
        if torch > lower:
            return name
    return "pitch black"


def simulator_torch(samples: int = 20000, seed: int = 0):
    """The torch the ENV draws, sampled the way the env draws it."""
    import random

    from env.expedition_env import TORCH_LEVEL_DECILES

    rng = random.Random(seed)
    out = []
    for _ in range(samples):
        u = rng.random() * 10.0
        i = min(int(u), 9)
        lo, hi = TORCH_LEVEL_DECILES[i], TORCH_LEVEL_DECILES[i + 1]
        out.append(lo + (hi - lo) * (u - i))
    return out


def distribution(paths) -> None:
    """Print what real play looked like, beside what the simulator samples."""
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in paths)
    torches = [int(t) for t in TORCH_LINE.findall(text)]
    quests = Counter(TIER_LINE.findall(text))
    stress = [(m[0], int(m[3])) for m in STRESS_LINE.findall(text)]

    print("\n-- the distribution real play actually ran in " + "-" * 26)
    if torches:
        sim = simulator_torch()
        real_b = Counter(band_of(t) for t in torches)
        sim_b = Counter(band_of(t) for t in sim)
        n, m = len(torches), len(sim)
        print(f"\nTORCH   n={n} observed, against {m} draws from "
              f"TORCH_LEVEL_DECILES")
        print(f"  {'band':13s} {'sim':>7s} {'real':>7s}   "
              f"stress chance / damage the band adds")
        for lower, name, chance, damage in BANDS:
            print(f"  {name:13s} {100 * sim_b[name] / m:6.1f}% "
                  f"{100 * real_b[name] / n:6.1f}%   "
                  f"+{chance:.0f} / +{damage:.0f}%")
        print(f"  {'mean torch':13s} {sum(sim) / m:6.1f}  {sum(torches) / n:6.1f}")
        pitch_sim = 100 * sim_b["pitch black"] / m
        pitch_real = 100 * real_b["pitch black"] / n
        if pitch_sim < 0.5 <= pitch_real:
            print(f"\n  ** TORCH 0 IS UNREACHABLE IN TRAINING and is "
                  f"{pitch_real:.1f}% of real play. **")
            print(f"     `TORCH_LEVEL_DECILES` starts at 3.0, so the sampler "
                  f"never draws the")
            print(f"     pitch-black band -- the one that adds +35 stress "
                  f"chance, +40% stress")
            print(f"     damage and +5 monster crit. The bridge's own torch "
                  f"layer puts the party")
            print(f"     there on purpose, for the +30% gold.")
    if quests:
        print(f"\nQUEST   " + ", ".join(f"{z} tier {t} x{n}"
                                        for (z, t), n in quests.most_common()))
    if stress:
        values = [s for _h, s in stress]
        values.sort()
        per_hero = {}
        for hero, value in stress:
            per_hero.setdefault(hero, []).append(value)
        print(f"\nSTRESS  n={len(values)} readings, "
              f"median {values[len(values) // 2]}, "
              f"mean {sum(values) / len(values):.1f}, max {values[-1]}")
        over = sum(1 for v in values if v >= 100)
        print(f"        {100 * over / len(values):.1f}% of readings are at or "
              f"over the 100 Resolve Check")
        worst = sorted(per_hero.items(), key=lambda kv: -max(kv[1]))[:4]
        print("        worst: " + ", ".join(f"{h} {max(v)}" for h, v in worst))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="*", default=None,
                    help="play logs to pool (default: bat/play.log)")
    ap.add_argument("--log", action="append", default=[],
                    help="another log to pool in")
    ap.add_argument("--distribution", action="store_true",
                    help="also print what real play ran in, beside what the "
                         "simulator samples -- the answer to 'why is stress "
                         "higher live than in the eval'")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    paths = [Path(p) for p in (list(args.logs) + list(args.log))]
    if not paths:
        paths = [root / "bat" / "play.log"]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        print("no play log found - run bat/combat_play.bat first")
        return 1

    from data.store_cache import load_store_cached
    from env.gym_env import CSV_DIR, GAME_DIR

    store = load_store_cached(CSV_DIR, GAME_DIR)
    control = control_display_names(store)

    entries = []
    for path in paths:
        got = parse(path)
        print(f"{path}: {len(got)} executed decisions")
        entries += got
    if not entries:
        print("nothing executed in these logs")
        return 1

    counts = grade(entries, control)
    print(f"\nreal play | {len(entries)} executed decisions | "
          f"{len(control)} skills classed as control")
    print("  the alternatives are the policy's own SHORTLIST, not the legal "
          "set, so every")
    print("  baseline below is against its own top choices -- a conservative "
          "form of each claim")
    print()

    if counts.get("graded"):
        got = 100.0 * counts["repeated"] / counts["graded"]
        want = 100.0 * counts["expected_repeat"] / counts["graded"]
        print(f"FOCUS   {got:.1f}% of follow-up attacks hit an enemy already "
              f"struck this round  (n={counts['graded']})")
        print(f"        a uniform pick over the shortlist would repeat {want:.1f}%")
        print(f"        -> {got - want:+.1f}pp")
    if counts.get("choices_with_hurt"):
        got = 100.0 * counts["left_standing"] / counts["choices_with_hurt"]
        want = 100.0 * counts["expected_left"] / counts["choices_with_hurt"]
        print(f"FINISH  {got:.1f}% of decisions with a weaker enemy in the "
              f"shortlist hit a HEALTHIER one  (n={counts['choices_with_hurt']})")
        print(f"        a uniform pick would do that {want:.1f}% of the time")
        print(f"        -> {got - want:+.1f}pp")
    if counts.get("control_offered"):
        got = 100.0 * counts["control_taken"] / counts["control_offered"]
        want = 100.0 * counts["expected_control"] / counts["control_offered"]
        print(f"CONTROL {got:.1f}% of turns took a control skill when one was "
              f"shortlisted  (n={counts['control_offered']})")
        print(f"        control was {want:.1f}% of the shortlist on those turns")
        print(f"        -> {got - want:+.1f}pp")

    heroes = Counter(e["hero"] for e in entries)
    print(f"\n  heroes seen: " + ", ".join(f"{h} {n}" for h, n in heroes.most_common(8)))
    if args.distribution:
        distribution(paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
