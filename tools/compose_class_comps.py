"""Give a class with NO comps enough of a training pool to be learnable.

`build_comp_pool` iterates `store.comp_loadouts`, so a class that appears in no
comp file is never sampled, never trained on, and — measured by
`--hold-out-class` — played about as well as a class the policy has never seen,
which is badly. The Sibyl arrives with exactly that problem: she is the first
third-party class this project loads properly, and there are zero comps with
her in them.

`composed_comps/` is the sanctioned answer and already exists for the same
reason: six machine recombinations of human-authored hero blocks, written when
the Duelist had two comps and the Runaway one. This is the tool that makes
more, and it is written for the general case because "the first modded class"
implies a second.

## What is composed, and what is not

Three of the four slots are copied **verbatim** from a curated comp Fran wrote.
Only one slot is replaced. That is what "recombination of human-authored
blocks" means here — the tool invents a single hero block and rearranges
nothing else.

The invented block is as close to not-a-choice as the data allows:

* **Skills**: every skill, when the class declares
  `can_select_combat_skills false` (the Abomination, the Duelist and the Sibyl
  — there is no 4-of-7 to pick). Otherwise the first four the game lists, which
  is a placement, not a recommendation; `--loadout-random-p` is what varies it
  during training.
* **Camp skills**: the class's own before the shared ones, capped at four.
* **Trinkets**: two of its class-locked, unlimited ones. Class-locked because
  those are the pair the mod author designed around the kit, and unlimited
  because a one-off is a thing you own once.
* **Quirks**: none. The search does not pick quirks and neither should this;
  an invented quirk sheet would flatter every measurement made on these comps.

## Two failure modes this must not reproduce

Both are pinned by `tests/data/test_comp_loadout.py` and both are how recombination
went wrong the first time:

* **The one-off trinket budget is per ID, comp-wide.** Three comps each legally
  holding one Ancestor's Candle compose into a party holding three. Checked
  after the swap, and the composition is rejected rather than repaired.
* **A display name that resolves to nothing is dropped SILENTLY**, leaving a
  hero wearing one trinket or none. So every name this tool writes is resolved
  through the real loader maps first, and a composition whose names do not all
  resolve is refused.

These comps are TRAINING COVERAGE, not proposals. They go to
`composed_comps/`, nobody has played them, and no fitness chose them.

    python tools/compose_class_comps.py --class sibyl_ms --count 4
    python tools/compose_class_comps.py --class sibyl_ms --count 4 --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.comp_loadout_loader import (HERO_DISPLAY_NAME_TO_ID,
                                      CompLoadoutLoader)
from data.store_cache import load_store_cached
from env.expedition_env import zone_id_for_location
from env.gym_env import CSV_DIR, GAME_DIR

CURATED = Path(CSV_DIR) / "metadata" / "COMPS" / "curated_comps"
COMPOSED = Path(CSV_DIR) / "metadata" / "COMPS" / "composed_comps"

# How many skills a hero that CAN choose ends up with. Mirrors
# env.expedition_env.SKILLS_PER_HERO rather than importing it, so this tool
# does not drag the env import chain in for one integer.
SKILLS_PER_HERO = 4
TRINKETS_PER_HERO = 2
CAMP_SKILLS_PER_HERO = 4

# The exact string `tests/data/test_comp_loadout.py` requires on every file in
# composed_comps/ and forbids on every file in curated_comps/.
COMPOSED_MARKER = "composed from curated hero blocks; no fitness involved"


def _reverse(name_map: dict[str, str]) -> dict[str, str]:
    """{id: display name}, keeping the FIRST display name for an id.

    The maps run display -> id and several display names can share an id (the
    string table carries localisation-only duplicates). First-wins matches how
    the loader resolves, so a name written here reads back to the same id.
    """
    out: dict[str, str] = {}
    for display, ident in name_map.items():
        out.setdefault(ident, display)
    return out


def build_hero_block(store, hero_id: str, game_dir: Path) -> dict | None:
    """The one invented slot. `None` if anything about it fails to resolve."""
    hero = store.heroes.get(hero_id)
    if hero is None:
        return None

    skill_names = CompLoadoutLoader._build_skill_name_maps(
        game_dir, set(store.heroes)).get(hero_id, {})
    id_to_skill = _reverse(skill_names)
    # `move` is the combat_move_skill, never an equipped slot.
    ordered = [s.id for s in hero.skills if s.id != "move"]
    if not hero.can_select_combat_skills:
        chosen = ordered
    else:
        chosen = ordered[:SKILLS_PER_HERO]
    skills = [id_to_skill.get(sid) for sid in chosen]
    if any(n is None for n in skills):
        return None

    camp_map = CompLoadoutLoader._build_camp_skill_name_map(game_dir)
    id_to_camp = _reverse(camp_map)
    own, shared = [], []
    for camp in store.camping_skills.values():
        if hero_id not in (camp.hero_classes or []):
            continue
        (own if len(camp.hero_classes) <= 4 else shared).append(camp.id)
    camp_ids = (sorted(own) + sorted(shared))[:CAMP_SKILLS_PER_HERO]
    camp_names = [id_to_camp.get(cid) for cid in camp_ids]
    camp_names = [n for n in camp_names if n]

    trinket_map = CompLoadoutLoader._build_trinket_name_map(
        game_dir, set(store.trinkets))
    id_to_trinket = _reverse(trinket_map)
    # Preference order, best first. The class's OWN trinkets say something
    # about how it is meant to be played, so a generic one is the last resort
    # rather than an equal option.
    def _wearable(pred):
        return sorted(tid for tid, t in store.trinkets.items()
                      if tid in id_to_trinket and pred(tid, t))

    def _locked(tid, t):
        return hero_id in (t.class_requirements or ())

    unlimited = _wearable(lambda tid, t: _locked(tid, t)
                          and not getattr(t, "limit", 0))
    one_off = _wearable(lambda tid, t: _locked(tid, t)
                        and getattr(t, "limit", 0))
    # A one-off is legal and is the ORDINARY case for a class trinket: 18 of
    # the 29 gate-passing modded classes have 7-13 class trinkets and not one
    # unlimited. `compose` refuses a party wearing the same one-off twice, so
    # uniqueness is already enforced one level up.
    generic = _wearable(lambda tid, t: not (t.class_requirements or ())
                        and not getattr(t, "limit", 0))

    chosen: list[str] = []
    for pool in (unlimited, one_off, generic):
        for tid in pool:
            if len(chosen) >= TRINKETS_PER_HERO:
                break
            if tid not in chosen:
                chosen.append(tid)
        if len(chosen) >= TRINKETS_PER_HERO:
            break
    if len(chosen) < TRINKETS_PER_HERO:
        return None
    trinkets = [id_to_trinket[t] for t in chosen[:TRINKETS_PER_HERO]]

    display = _reverse(HERO_DISPLAY_NAME_TO_ID).get(hero_id, hero_id)
    return {
        "heroClass": display,
        "activeSkills": skills,
        "activeCampSkills": camp_names,
        "trinket1": trinkets[0],
        "trinket2": trinkets[1],
        # Deliberately empty -- see the module docstring.
        "quirks": {"positive": [], "negative": []},
        "lockedQuirks": {"positive": [], "negative": []},
    }


def _one_off_ids(store, comp: dict, trinket_map: dict[str, str]) -> list[str]:
    out = []
    for hero in comp["heroes"]:
        for key in ("trinket1", "trinket2"):
            tid = trinket_map.get(hero.get(key) or "")
            if tid and getattr(store.trinkets.get(tid), "limit", 0):
                out.append(tid)
    return out


def compose(store, source: dict, block: dict, slot: int, name: str,
            trinket_map: dict[str, str]) -> dict | None:
    """`source` with `slot` replaced by `block`, or None if the result is
    illegal. Every other slot is copied verbatim."""
    heroes = [dict(h) for h in source.get("heroes", []) if h.get("heroClass")]
    if len(heroes) != 4 or not 0 <= slot < 4:
        return None
    heroes[slot] = block
    out = {
        "teamName": name,
        "location": source.get("location", ""),
        "heroes": heroes,
        # `provenance.authoredBy` is a CONVENTION, not decoration.
        # `tests/data/test_comp_loadout.py` requires every file in composed_comps/
        # to carry this exact marker and forbids it in curated_comps/, so a
        # file in the wrong folder fails instead of silently counting as a comp
        # Fran played and vouches for. That test caught this tool omitting it.
        "provenance": {
            "authoredBy": COMPOSED_MARKER,
            "why": ("training-coverage comp for a class with no comps of its "
                    "own. GA output is a recommendation for Fran to PLAY and "
                    "is deliberately NOT promoted into the training pool: a "
                    "comp selected by the policy's own score and then trained "
                    "on closes the loop."),
            "sources": [f"{source.get('teamName', '')} (rank {slot + 1} "
                        f"replaced by {block['heroClass']})"],
            "repairs": [],
        },
        "composedFrom": {
            "source": source.get("teamName", ""),
            "replacedRank": slot + 1,
            "note": ("training coverage only -- nobody has played this and no "
                     "fitness chose it. See tools/compose_class_comps.py."),
        },
    }
    one_offs = _one_off_ids(store, out, trinket_map)
    if len(one_offs) != len(set(one_offs)):
        return None            # two heroes wearing the same one-off
    return out


def curated_sources() -> list[dict]:
    """Curated comps with a full four, one per class multiset, stable order.

    Hoisted out of `main` so a batch reads them once. Diversity of the three
    KEPT slots is the whole value of a composed comp, so the de-duplication on
    class multiset is load-bearing rather than tidiness.
    """
    sources, seen = [], set()
    for path in sorted(CURATED.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        classes = tuple(h.get("heroClass") for h in data.get("heroes", [])
                        if h.get("heroClass"))
        if len(classes) != 4 or tuple(sorted(classes)) in seen:
            continue
        # A LOCATION THAT RESOLVES TO NOTHING IS DROPPED FROM THE TRAINABLE
        # POOL, silently -- `ExpeditionEnv` routes by this field and there is
        # no enemy pool for the Farmstead, the Courtyard or Darkest Dungeon II.
        # The first run of this tool composed four comps and only three ever
        # reached training, because one source was tagged "The Farmstead".
        if zone_id_for_location(data.get("location", "")) is None:
            continue
        seen.add(tuple(sorted(classes)))
        sources.append(data)
    return sources


def compose_for_class(store, hero_class: str, count: int, game_dir: Path,
                      sources: list[dict], trinket_map: dict,
                      prefix: str | None = None) -> tuple[list, str]:
    """`(written, reason)` -- `written` empty means nothing was composed."""
    if hero_class not in store.heroes:
        return [], "not in the store (is DDSIM_MOD_DIRS set?)"
    block = build_hero_block(store, hero_class, game_dir)
    if block is None:
        return [], ("no hero block: skills, camp skills or the two "
                    "class-locked trinkets do not all resolve to display names")

    name_prefix = prefix or block["heroClass"]
    written = []
    for i in range(count):
        if not sources:
            break
        source = sources[(i * 7) % len(sources)]      # spread across the list
        slot = 3 - (i % 4)                            # vary the rank she takes
        name = f"{name_prefix} Coverage {i + 1:02d}"
        comp = compose(store, source, block, slot, name, trinket_map)
        if comp is None:
            continue
        out_path = COMPOSED / (name.replace(" ", "_").replace(":", "") + ".json")
        written.append((out_path, comp))
    if not written:
        return [], "every candidate was rejected as illegal"
    return written, ""


def gate_passing_classes(path: Path) -> list[str]:
    """The modded classes `tools/class_visibility.py` cleared.

    Vanilla is excluded because it already has 176 curated comps; this tool
    exists to give a class with NO comps a training pool.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [r["hero"] for r in data["classes"]
            if r["passes"] and not r["vanilla"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--class", dest="hero_class", default=None,
                    help="hero id(s) to give coverage to, comma-separated, "
                         "e.g. sibyl_ms or sibyl_ms,falconer")
    ap.add_argument("--from-gate", default=None, metavar="PATH",
                    help="compose for every MODDED class that passes "
                         "tools/class_visibility.py --json PATH. A class the "
                         "gate refused must not enter the corpus: the policy "
                         "would be trained on skills it cannot see.")
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written and write nothing")
    ap.add_argument("--prefix", default=None,
                    help="team-name prefix; defaults to the class's display name")
    args = ap.parse_args()
    if not args.hero_class and not args.from_gate:
        ap.error("give --class or --from-gate")

    game_dir = Path(GAME_DIR)
    store = load_store_cached(CSV_DIR, GAME_DIR)

    if args.from_gate:
        classes = gate_passing_classes(Path(args.from_gate))
    else:
        classes = [c.strip() for c in args.hero_class.split(",") if c.strip()]
    if len(classes) > 1 and args.prefix:
        ap.error("--prefix names one comp family; it cannot span classes")

    trinket_map = CompLoadoutLoader._build_trinket_name_map(
        game_dir, set(store.trinkets))
    sources = curated_sources()

    total, failed = 0, []
    for hero_class in classes:
        written, reason = compose_for_class(
            store, hero_class, args.count, game_dir, sources, trinket_map,
            args.prefix)
        if not written:
            failed.append((hero_class, reason))
            print(f"{hero_class:<24} SKIPPED -- {reason}")
            continue
        print(f"{hero_class:<24} {len(written)} comps")
        for path, comp in written:
            ranks = " / ".join(h["heroClass"] for h in comp["heroes"])
            print(f"    rank {comp['composedFrom']['replacedRank']} of "
                  f"{comp['composedFrom']['source']!r} -> {ranks}")
            if not args.dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(comp, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
        total += len(written)

    verb = "would write" if args.dry_run else "wrote"
    print(f"\n{verb} {total} comps for {len(classes) - len(failed)} of "
          f"{len(classes)} classes -> {COMPOSED}")
    if failed:
        print(f"{len(failed)} class(es) composed nothing:")
        for hero_class, reason in failed:
            print(f"  {hero_class:<24}{reason}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
