"""Which classes and which skills does the policy actually prefer?

A RULER. Nothing here may become a reward term, an action mask or a training
target: a policy fitted to a usage profile is a policy imitating its own past
play, and the tier column would additionally fit it to one player's opinion of
seventeen classes. `tests/suite/test_rulers.py` enforces that.

## The unit is PICK RATE WHEN OFFERED, and the denominator is the whole point

Raw usage is not preference. A skill is not on the board when it is not in the
equipped four, when the hero stands in a rank it cannot launch from, when its
per-battle limit is spent, or when nothing it can target is alive -- and
counting those turns against it is the mistake this project has already made
once, reading a Vestal who played Illumination on 0.8% of turns as an
indictment of Illumination. Niche skills are rare BY DESIGN.

So every decision contributes:

* **one pick** for the skill the policy chose, and
* **one opportunity for each DISTINCT skill that was legal at that moment**,

read off `_action_map`, which is the table `step` itself consumes. A skill
offered against three target ranks is one opportunity, not three -- the
question is "was it available", asked once per decision.

`opportunities` is therefore an availability control accumulated per decision
rather than modelled, the same device `tools/style_probe.py` uses for heals and
`tools/team_probe.py` for focus-fire. A skill with zero opportunities is
EXCLUDED from every rate, never scored as zero: "never offered" and "offered
and refused" are opposite findings.

## Why this shape, exactly

`(picks, opportunities)` is the pair Fran's team-builder already scores:
`src/utils/generalistStats.js::scoreItems` turns it into
`rate -> smoothed -> score -> rank`, with an `alpha` knob interpolating between
raw popularity and pure adoption rate, and a prior that stops 5/5 outranking
50/50. `--json` therefore emits **exactly the shape `buildUsageStats` returns**,
so the app scores the policy with the same code it scores the comp library
with, and the two become directly comparable. The name mapping is instrumented
rather than trusted (see `--json` output's `unmapped`).

## Classes are answered twice, because the policy does not choose them

Comps are handed to it, so there is no such thing as a class pick rate. Two
honest questions remain and both are reported:

* **outcome** -- the run score, success, deaths and stress of the comps that
  field the class. Confounded by comp quality and by the training sampler, and
  the JSON carries the standard error so a gap can be read against its noise.
* **behaviour** -- turns taken, the intent mix, how many of the skills it was
  OFFERED it ever takes, and how often it took the expert's higher-tier option when a
  real choice was on offer (graded only when at least two DIFFERENT tiers were
  available, and against a uniform baseline accumulated per decision --
  otherwise "it took an A" scores as good play when an A was all it had).

    python tools/model_preference.py --model models/mods54_knobs_s0.zip \\
        --json archive/logs/model_pref_s0.json
    python tools/model_preference.py --model <ckpt> --self-check

**Set `DDSIM_MOD_DIRS` to the roots the checkpoint trained with.** Unset, the
store is vanilla-only: 20 classes and 176 comps instead of 54 and 288, with no
error anywhere, because a missing content root produces a smaller store rather
than a failure. This tool refuses rather than measure the wrong world.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge.rollout import Rollout
from data.comp_loadout_loader import HERO_DISPLAY_NAME_TO_ID, CompLoadoutLoader
from data.mod_roots import MOD_DIRS_ENV, mod_roots
from data.store_cache import load_store_cached
from env.expedition_env import build_comp_pool, zone_id_for_location
from env.gym_env import CSV_DIR, GAME_DIR
from tools.skill_tiers import TIER_RANK, tier_index
from tools.style_probe import CATEGORIES, classify, party_of

# Where the game's own display name is not the key Fran's team-builder uses.
# Every row here was checked against the app's data rather than guessed, and
# the one that looked obvious was WRONG: `croc` localizes to "Trophy Hunter"
# and the app's "Crocodilian" is a different mod entirely (modId 1959183997,
# sharing exactly one skill name), so it gets no row and is reported unmapped.
#
#   man_at_arms  the app writes it unhyphenated
#   dd2duelist   localizes to "Duelist", which `duelist` already claims -- a
#                display-name collision would silently merge two classes into
#                one row, so the app's own key breaks the tie
CLASS_ALIASES = {
    "man_at_arms": "Man at Arms",
    "dd2duelist": "Dd2Duelist",
}

#: A pseudo-skill id (`__pass__`, `__move__`) is a decision but not a skill.
INTERNAL = "__"

# The app's own hero files, read the way `tools/skill_tiers.py` reads its tier
# list: an absolute path, like GAME_DIR and for the same reason, and a missing
# file is not an error -- the export is still correct, it just cannot say which
# names the app will recognise.
APP_DATA = Path(r"F:\Personal_Fran\Programas\Softwares\nextjs_projects"
                r"\dd-team-builder\src\data")
_APP_CLASS_RE = re.compile(r"^  '((?:[^'\\]|\\.)*)': \{", re.M)


def app_class_keys(base: Path | None = None) -> set[str]:
    """Every hero-class key `heroes.js` and `modded_heroes.js` define.

    A name this set does not contain still exports -- it is a real class the
    policy really played -- but it will not join onto the library source, so
    the app can show no comparison for it and the export says so instead of
    letting a reader assume the join worked. `croc` is the live example: it
    localizes to "Trophy Hunter" and the app has no such class, while the app's
    "Crocodilian" is a different mod sharing one skill name.
    """
    root = base or APP_DATA
    out: set[str] = set()
    for name in ("heroes.js", "modded_heroes.js"):
        try:
            text = (root / name).read_text(encoding="utf-8")
        except OSError:
            continue
        out.update(m.group(1).replace("\\'", "'") for m in _APP_CLASS_RE.finditer(text))
    return out


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=Path(__file__).resolve().parent.parent,
                              capture_output=True, text=True, timeout=10,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def display_names(store) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """({hero_id: class display}, {hero_id: {skill_id: skill display}}).

    Both are the INVERSE of the maps `data/comp_loadout_loader.py` builds to
    read a comp file, and are resolved through the game's own localization for
    the same reason `tools/skill_tiers.py` is: the game says "First Aid" where
    the string table says "Wound Care" and both are `first_aid`, so guessing a
    display name from an id is wrong in both directions.
    """
    loc = CompLoadoutLoader._load_english_entries(Path(GAME_DIR), "hero_class_name_")
    shipped = {v: k for k, v in HERO_DISPLAY_NAME_TO_ID.items()}
    classes = {}
    for hero_id in store.heroes:
        classes[hero_id] = CLASS_ALIASES.get(
            hero_id, loc.get(hero_id) or shipped.get(hero_id)
            or hero_id.replace("_", " ").title())
    skill_maps = CompLoadoutLoader._build_skill_name_maps(
        Path(GAME_DIR), set(store.heroes))
    skills = {}
    for hero_id, by_name in skill_maps.items():
        # First display name wins, deterministically, so two localization
        # entries pointing at one id cannot make the export depend on dict
        # ordering.
        out: dict[str, str] = {}
        for name in sorted(by_name):
            out.setdefault(by_name[name], name)
        skills[hero_id] = out
    return classes, skills


class Record:
    """Everything one sweep accumulates. One object so `--self-check` can
    compare two sweeps field by field."""

    def __init__(self) -> None:
        self.picks: Counter = Counter()          # (hero_id, skill_id) -> n
        self.offers: Counter = Counter()         # (hero_id, skill_id) -> n
        self.pick_comps: dict = defaultdict(set)
        self.offer_comps: dict = defaultdict(set)
        self.turns: Counter = Counter()          # hero_id -> decisions
        self.mix: dict = defaultdict(Counter)    # hero_id -> category -> n
        self.tier: dict = defaultdict(Counter)   # hero_id -> tier stat -> n
        self.slots: Counter = Counter()          # hero_id -> comp appearances
        self.slot_comps: dict = defaultdict(set)
        self.equipped: dict = defaultdict(set)   # hero_id -> skill ids fielded
        self.outcomes: dict = defaultdict(list)  # hero_id -> [per-comp stats]
        self.decisions = 0

    def signature(self) -> tuple:
        """What `--self-check` compares. Sets are sorted so the comparison is
        of content and never of iteration order."""
        return (
            sorted(self.picks.items()), sorted(self.offers.items()),
            sorted(self.turns.items()), self.decisions,
            sorted((h, sorted(c.items())) for h, c in self.mix.items()),
            sorted((h, sorted(c.items())) for h, c in self.tier.items()),
        )


def _grade_tiers(offered: list[str], mine: str | None, rec: Record, hero_id: str) -> None:
    """The expert-tier block, per class.

    Lifted from `tools/style_probe.py`'s counting_step, which is where it was
    first argued for, and it keeps both of that version's rules: grade only
    when at least two DIFFERENT tiers were on offer, so "it took an A" is not
    scored as a good decision when an A was the only thing available; and
    accumulate the uniform baseline PER DECISION, because how many options are
    on offer varies by hero, rank and round, so it cannot be assumed.
    """
    if not mine or len({*offered}) < 2:
        return
    best = min(offered, key=lambda t: TIER_RANK[t])
    stats = rec.tier[hero_id]
    stats["graded"] += 1
    stats[f"played_{mine}"] += 1
    if mine == best:
        stats["took_best"] += 1
    stats["regret_sum"] += TIER_RANK[mine] - TIER_RANK[best]
    n = len(offered)
    stats["rand_best_sum"] += sum(1 for t in offered if t == best) / n
    stats["rand_regret_sum"] += sum(
        TIER_RANK[t] - TIER_RANK[best] for t in offered) / n


def play_comp(rollout, party, zone_id, comp_name, args, tiers, rec: Record):
    """Play one comp and fold every decision into `rec`. Returns the result.

    Hooks `type(base).step`, the device `tools/comp_ga.py::_record_skill_use`
    and `tools/style_probe.py::play_and_count` both use, because `_action_map`
    is the table `step` itself reads -- so what lands in the counters is the
    decision the policy made rather than a reconstruction of it.

    The raw action index is deliberately never decoded: `_slot_order` is a
    per-episode permutation of the hero's movepool, so slot k is a different
    skill every episode.
    """
    env = rollout.env
    base = env.unwrapped
    store = base.store
    original = type(base).step

    def counting_step(self, action):
        actor = self.engine.get_current_actor_id()
        mapped = self._action_map.get(action)
        if actor is None or mapped is None:
            return original(self, action)
        hero = next((h for h in self.engine.heroes if h.id == actor), None)
        if hero is None:
            return original(self, action)
        cls = hero.definition_id
        rec.decisions += 1
        rec.turns[cls] += 1

        chosen = mapped.skill_id
        data = None
        if not chosen.startswith(INTERNAL):
            data = self.engine.get_skill_data(chosen, level=hero.skill_tier)
        rec.mix[cls][classify(data, chosen, store)] += 1

        # THE AVAILABILITY CONTROL. Deduped by skill id: a skill legal against
        # three target ranks occupies three action indices and is still one
        # thing that was on offer.
        offered = {other.skill_id for other in self._action_map.values()
                   if other is not None and not other.skill_id.startswith(INTERNAL)}
        for sid in offered:
            rec.offers[(cls, sid)] += 1
            rec.offer_comps[(cls, sid)].add(comp_name)
        if not chosen.startswith(INTERNAL):
            rec.picks[(cls, chosen)] += 1
            rec.pick_comps[(cls, chosen)].add(comp_name)

        if tiers:
            _grade_tiers([t for t in (tiers.get((cls, s)) for s in offered) if t],
                         tiers.get((cls, chosen)), rec, cls)
        return original(self, action)

    type(base).step = counting_step
    try:
        result = rollout.evaluate(party, zone_id, args.tier, args.combats,
                                  length_band=args.band, episodes=args.episodes,
                                  seed=args.seed)
    finally:
        type(base).step = original
    return result


def sweep(rollout, rows, args, tiers) -> tuple[Record, list]:
    """Play every comp in `rows`, in order. Returns (record, per-comp results)."""
    rec = Record()
    played = []
    for i, (name, zone_id, loadout) in enumerate(rows):
        party = party_of(loadout)
        classes = [m.hero_class for m in party]
        for cls in classes:
            rec.slots[cls] += 1
            rec.slot_comps[cls].add(name)
        for member in party:
            rec.equipped[member.hero_class].update(member.skill_ids or ())
        result = play_comp(rollout, party, zone_id, name, args, tiers, rec)
        for cls in classes:
            rec.outcomes[cls].append({
                "run_score": result.mean_run_score,
                "success": result.success_rate,
                "zero_death": result.zero_death_rate,
                "deaths": result.mean_deaths,
                "stress": result.mean_final_stress,
                "quality": result.mean_quality,
            })
        played.append((name, zone_id, classes, result))
        if not args.quiet:
            print(f"  [{i + 1:3d}/{len(rows)}] {name[:34]:36s} "
                  f"score {result.mean_run_score * 100:5.1f}  "
                  f"succ {result.success_rate * 100:5.1f}%", flush=True)
    return rec, played


# --------------------------------------------------------------------------
# The export. Shape is `buildUsageStats`'s, field for field, so that
# `scoreItems` consumes it with no change at all.
# --------------------------------------------------------------------------

def _mean(values, key):
    vals = [v[key] for v in values]
    return statistics.fmean(vals) if vals else 0.0


def _sem(values, key):
    vals = [v[key] for v in values]
    if len(vals) < 2:
        return 0.0
    return statistics.stdev(vals) / (len(vals) ** 0.5)


def build_export(rec: Record, played, store, args, tiers, class_name, skill_name) -> dict:
    """`rec` -> the JSON the team-builder's scorer reads.

    Two denominators live in here and they are NOT the same number, which is
    worth stating because the app's own hero-class filter switches between
    them:

    * an item's `opportunities` is how many decisions OFFERED that skill, so
      the unfiltered ranking is a pick-rate-when-offered;
    * `classUsage[cls]` is how many decisions that class MADE, so filtering to
      one class gives a share-of-turns instead.

    Both are real; neither is the other. `offeredByClass` is carried beside
    `byClass` so a reader that wants the per-class offered-rate has it without
    a second run.
    """
    unmapped: dict[str, list[str]] = {"classes": [], "skills": []}
    app_keys = app_class_keys()
    no_app_entry: list[str] = []
    comp_names = {name for name, _z, _c, _r in played}
    n_comps = len(comp_names)

    def cls_display(hero_id: str) -> str:
        name = class_name.get(hero_id)
        if name is None:
            unmapped["classes"].append(hero_id)
            return hero_id
        if app_keys and name not in app_keys and name not in no_app_entry:
            no_app_entry.append(name)
        return name

    # ---- skills ---------------------------------------------------------
    by_skill: dict[str, dict] = {}
    for (hero_id, skill_id), offers in rec.offers.items():
        name = skill_name.get(hero_id, {}).get(skill_id)
        if name is None:
            unmapped["skills"].append(f"{hero_id}:{skill_id}")
            name = skill_id
        entry = by_skill.setdefault(name, {
            "id": "skills:" + name, "kind": "skills", "name": name,
            "universal": False, "owners": [], "simIds": [],
            "picks": {"slots": 0, "families": 0},
            "opportunities": {"slots": 0, "families": 0},
            "byClass": {}, "offeredByClass": {}, "tier": None,
            "_pick_comps": set(), "_offer_comps": set(),
        })
        display = cls_display(hero_id)
        if display not in entry["owners"]:
            entry["owners"].append(display)
        sim_id = f"{hero_id}:{skill_id}"
        if sim_id not in entry["simIds"]:
            entry["simIds"].append(sim_id)
        picks = rec.picks.get((hero_id, skill_id), 0)
        entry["picks"]["slots"] += picks
        entry["opportunities"]["slots"] += offers
        entry["_pick_comps"] |= rec.pick_comps.get((hero_id, skill_id), set())
        entry["_offer_comps"] |= rec.offer_comps.get((hero_id, skill_id), set())
        bucket = entry["byClass"].setdefault(display, {"slots": 0, "families": 0})
        bucket["slots"] += picks
        bucket["families"] += len(rec.pick_comps.get((hero_id, skill_id), set()))
        offered = entry["offeredByClass"].setdefault(display, {"slots": 0, "families": 0})
        offered["slots"] += offers
        offered["families"] += len(rec.offer_comps.get((hero_id, skill_id), set()))
        if entry["tier"] is None:
            entry["tier"] = tiers.get((hero_id, skill_id))

    skills = []
    for entry in by_skill.values():
        entry["picks"]["families"] = len(entry.pop("_pick_comps"))
        entry["opportunities"]["families"] = len(entry.pop("_offer_comps"))
        # A pick is by definition an offer, so this can only ever be a no-op;
        # it is here because the app relies on the invariant and a silent
        # violation would read as a rate above 1.
        for unit in ("slots", "families"):
            entry["opportunities"][unit] = max(entry["opportunities"][unit],
                                               entry["picks"][unit])
        entry["owners"].sort()
        skills.append(entry)
    skills.sort(key=lambda e: e["name"])

    # ---- heroes ---------------------------------------------------------
    heroes = []
    class_detail = {}
    for hero_id, turns in sorted(rec.turns.items()):
        display = cls_display(hero_id)
        outcomes = rec.outcomes.get(hero_id, [])
        # Breadth, availability-controlled like everything else here. Against
        # the movepool it is not a fact about the policy at all: a comp equips
        # four of seven, so 3/7 can be perfect play. The denominator that means
        # something is what was ever actually put in front of it.
        used = {sid for (h, sid) in rec.picks if h == hero_id and rec.picks[(h, sid)]}
        offered = {sid for (h, sid) in rec.offers if h == hero_id}
        equipped = rec.equipped.get(hero_id, set())
        movepool = {s.id for s in store.heroes[hero_id].skills
                    if s.id != "move"} if hero_id in store.heroes else set()
        tier_stats = rec.tier.get(hero_id, Counter())
        graded = tier_stats.get("graded", 0)
        mix_total = sum(rec.mix[hero_id][c] for c in CATEGORIES) or 1
        detail = {
            "simId": hero_id,
            "comps": len(rec.slot_comps.get(hero_id, set())),
            "turns": turns,
            "runScore": _mean(outcomes, "run_score"),
            "runScoreSe": _sem(outcomes, "run_score"),
            "success": _mean(outcomes, "success"),
            "zeroDeath": _mean(outcomes, "zero_death"),
            "deaths": _mean(outcomes, "deaths"),
            "stress": _mean(outcomes, "stress"),
            "quality": _mean(outcomes, "quality"),
            "skillsUsed": len(used),
            "skillsOffered": len(offered),
            "skillsEquipped": len(equipped),
            "skillsOwned": len(movepool),
            "mix": {c: rec.mix[hero_id][c] / mix_total for c in CATEGORIES},
            "tierGraded": graded,
            "tierBestRate": (tier_stats.get("took_best", 0) / graded) if graded else None,
            "tierBestRateUniform": (tier_stats.get("rand_best_sum", 0) / graded) if graded else None,
            "tierRegret": (tier_stats.get("regret_sum", 0) / graded) if graded else None,
            "tierRegretUniform": (tier_stats.get("rand_regret_sum", 0) / graded) if graded else None,
        }
        class_detail[display] = detail
        heroes.append({
            "id": "hero:" + display, "kind": "heroes", "name": display,
            "universal": True, "owners": [display],
            # Share of every decision the policy made. NOT a preference -- the
            # comps are handed to it and turn order is speed -- but it is the
            # honest thing to put where the library source puts slot share,
            # and `classDetail` carries what the class question actually turns
            # on.
            "picks": {"slots": turns,
                      "families": len(rec.slot_comps.get(hero_id, set()))},
            "opportunities": {"slots": rec.decisions, "families": n_comps},
            "byClass": {display: {"slots": turns,
                                  "families": len(rec.slot_comps.get(hero_id, set()))}},
            "detail": detail,
        })

    class_usage = {}
    for hero_id, turns in rec.turns.items():
        class_usage[cls_display(hero_id)] = {
            "slots": turns, "families": len(rec.slot_comps.get(hero_id, set()))}

    return {
        "source": "model",
        "provenance": {
            "model": args.model,
            "episodes": args.episodes,
            "comps": n_comps,
            "band": args.band,
            "tier": args.tier,
            "combats": args.combats,
            "seed": args.seed,
            "modRoots": len(mod_roots()),
            "classes": len(rec.turns),
            "decisions": rec.decisions,
            "gitSha": _git_sha(),
            "rankedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "unmapped": {k: sorted(set(v)) for k, v in unmapped.items()},
        # Classes the app has no entry for. They export normally; what they
        # cannot have is a library counterpart to be compared against.
        "noAppEntry": sorted(no_app_entry),
        "appKeysRead": len(app_keys),
        "compCount": n_comps,
        "familyCount": n_comps,
        "slotCount": rec.decisions,
        "classes": sorted(class_usage),
        "classUsage": class_usage,
        "items": {"heroes": heroes, "skills": skills},
        "classDetail": class_detail,
    }


# --------------------------------------------------------------------------


def report(export: dict, top: int) -> None:
    prov = export["provenance"]
    print(f"\n{prov['classes']} classes | {prov['comps']} comps x "
          f"{prov['episodes']} episodes | {prov['decisions']} decisions")

    detail = export["classDetail"]
    print("\nCLASS OUTCOME -- the comps that field it, ranked by run score.")
    print("  This is NOT a preference: the policy is handed its comps. It is "
          "confounded by\n  comp quality and by the training sampler, so read "
          "a gap against its standard error.")
    print(f"    {'class':24s} {'comps':>5s} {'run score':>12s} {'succ':>6s} "
          f"{'deaths':>7s} {'stress':>7s} {'used/offd':>10s} {'best tier':>10s}")
    ranked = sorted(detail.items(), key=lambda kv: -kv[1]["runScore"])
    for name, d in ranked[:top]:
        best = ("%5.1f%% vs %4.1f%%" % (100 * d["tierBestRate"],
                                        100 * d["tierBestRateUniform"])
                if d["tierBestRate"] is not None else "         --")
        print(f"    {name[:24]:24s} {d['comps']:5d} "
              f"{100 * d['runScore']:7.1f} +-{100 * d['runScoreSe']:4.1f} "
              f"{100 * d['success']:5.1f}% {d['deaths']:7.2f} {d['stress']:7.1f} "
              f"{d['skillsUsed']:5d}/{d['skillsOffered']:<4d} {best}")
    if len(ranked) > top:
        print(f"    ... {len(ranked) - top} more (all of them are in --json)")

    print("\nSKILL PREFERENCE -- picks / decisions where the skill was LEGAL.")
    print("  A skill never offered is excluded, not scored zero.")
    rows = [(e["picks"]["slots"] / e["opportunities"]["slots"], e)
            for e in export["items"]["skills"] if e["opportunities"]["slots"] > 0]
    rows.sort(key=lambda r: -r[0])
    print(f"    {'skill':28s} {'owner':20s} {'rate':>7s} {'picks':>7s} "
          f"{'offers':>7s}  tier")
    for rate, e in rows[:top]:
        print(f"    {e['name'][:28]:28s} {(e['owners'][0] if e['owners'] else ''):20s} "
              f"{100 * rate:6.1f}% {e['picks']['slots']:7d} "
              f"{e['opportunities']['slots']:7d}  {e['tier'] or '-'}")
    tail = rows[max(top, len(rows) - top):]
    if tail:
        print("    ---- least taken when offered ----")
    for rate, e in tail:
        print(f"    {e['name'][:28]:28s} {(e['owners'][0] if e['owners'] else ''):20s} "
              f"{100 * rate:6.1f}% {e['picks']['slots']:7d} "
              f"{e['opportunities']['slots']:7d}  {e['tier'] or '-'}")

    if not export["appKeysRead"]:
        print("\nthe team-builder's hero files were not readable, so this run "
              "cannot say\nwhich names the app will recognise")
    elif export["noAppEntry"]:
        print(f"\n{len(export['noAppEntry'])} class(es) the team-builder has no "
              f"entry for -- exported, but with\nno library counterpart to "
              f"compare against: {', '.join(export['noAppEntry'])}")

    unmapped = export["unmapped"]
    n = len(unmapped["classes"]) + len(unmapped["skills"])
    if n:
        print(f"\n{n} name(s) did not resolve to a team-builder key and are "
              f"exported under their sim id:")
        for kind, names in unmapped.items():
            if names:
                print(f"    {kind}: {', '.join(names[:12])}"
                      + (" ..." if len(names) > 12 else ""))
    else:
        print("\nevery class and skill name resolved to a team-builder key")


def load_rows(store, limit: int, zone_override: str | None):
    rows = []
    for name, zone, _classes in build_comp_pool(store):
        loadout = store.comp_loadout_by_name(name)
        if loadout is None or len(loadout.heroes) < 4:
            continue
        zone_id = (zone_override or zone_id_for_location(loadout.location)
                   or zone or "warrens")
        rows.append((name, zone_id, loadout))
    rows.sort(key=lambda r: r[0])
    if limit and len(rows) > limit:
        step = len(rows) / limit
        rows = [rows[int(i * step)] for i in range(limit)]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="models/mods54_knobs_s0.zip")
    ap.add_argument("--episodes", type=int, default=4)
    ap.add_argument("--tier", type=int, default=3)
    ap.add_argument("--band", default="long", choices=("short", "medium", "long"))
    ap.add_argument("--combats", type=int, default=15)
    ap.add_argument("--zone", default=None,
                    help="pin every comp to one zone; default is the comp's own")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0,
                    help="how many comps to play, spread evenly; 0 is all of them")
    ap.add_argument("--top", type=int, default=20, metavar="N",
                    help="rows per table in the printed report")
    ap.add_argument("--json", default=None, metavar="PATH")
    ap.add_argument("--quiet", action="store_true",
                    help="drop the per-comp progress lines")
    ap.add_argument("--allow-vanilla", action="store_true",
                    help=f"proceed with {MOD_DIRS_ENV} unset")
    ap.add_argument("--self-check", action="store_true",
                    help="sweep the same comps twice and require identical "
                         "counters; anything else means the harness moved")
    args = ap.parse_args()

    roots = mod_roots()
    if not roots and not args.allow_vanilla:
        print(f"REFUSING: {MOD_DIRS_ENV} is unset, so the store is vanilla-only "
              f"(20 classes, 176 comps).\nA checkpoint trained with mods would "
              f"be measured against a world two thirds of\nwhich is missing, "
              f"with no error anywhere. Set it to the roots the run trained\n"
              f"with -- `grep '^Mod roots: ' archive/logs/train_<run>.log` has "
              f"them verbatim --\nor pass --allow-vanilla if that is really "
              f"what you want.")
        return 2

    store = load_store_cached(CSV_DIR, GAME_DIR)
    tiers = tier_index(store, GAME_DIR)
    class_name, skill_name = display_names(store)
    rows = load_rows(store, args.limit, args.zone)

    print(f"model preference | {args.model}")
    print(f"  {len(roots)} mod roots, {len(store.heroes)} classes in the store, "
          f"{len(rows)} comps")
    print(f"  tier {args.tier} {args.band} x{args.combats} | "
          f"{args.episodes} episodes | seed {args.seed}")
    print(f"  expert tiers: {len(tiers)} (class, skill) pairs"
          if tiers else "  expert tier list not found -- the tier column is empty")

    rollout = Rollout(args.model)

    if args.self_check:
        check = rows[:6]
        print(f"\nself-check: sweeping {len(check)} comps twice")
        first, _ = sweep(rollout, check, args, tiers)
        second, _ = sweep(rollout, check, args, tiers)
        if first.signature() == second.signature():
            print("self-check PASSED: both sweeps identical "
                  f"({first.decisions} decisions)")
            return 0
        print("self-check FAILED: the two sweeps disagree, so nothing this "
              "tool prints can be believed")
        a, b = first.signature(), second.signature()
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print(f"  field {i} differs")
        return 1

    rec, played = sweep(rollout, rows, args, tiers)
    export = build_export(rec, played, store, args, tiers, class_name, skill_name)
    report(export, args.top)

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(export, fh, ensure_ascii=False, indent=1)
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
