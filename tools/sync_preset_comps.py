"""Pull the team-builder's `presetComps` into `metadata/COMPS/curated_comps/`.

Fran maintains the comp library in the `dd-team-builder` Next.js app and
exports it as `src/data/presetComps/*.json`. That is the same JSON this repo
already reads -- `teamName`, `location`, `heroes[]` with `heroClass`,
`activeSkills`, `activeCampSkills`, `trinket1/2`, `quirks` -- so the sync is a
copy plus the checks below, not a conversion.

**Why copy rather than point the loader at the app.** The comps are TRAINING
DATA. A checkpoint is only comparable in the world it trained in, and a world
that lives in another project's `src/` is one this repo cannot record, diff or
restore. Copying keeps `metadata/` the single versioned source, which is also
what `data/CLAUDE.md` documents: drop a file in `curated_comps/` and it trains.

**The guard that matters: a re-export renames every comp.** Measured
2026-09-19, the app's export shared exactly 2 team names with the 177 files
already here, while covering **all 169** of their class multisets with a
median skill-set and trinket overlap of 1.00. The same builds, renamed. So
this tool matches on the CLASS MULTISET and never on the name -- resolving by
name would have reported 175 comps deleted and 467 added, and a careless sync
on that reading would have thrown away every hand-tested comp in the repo.

It REFUSES to apply when a multiset present here is absent from the source,
because that is the one shape of mistake that loses a comp Fran vouched for.
`--allow-drops` overrides, and names what goes.

    python tools/sync_preset_comps.py                 # plan only
    python tools/sync_preset_comps.py --apply
    python tools/sync_preset_comps.py --source DIR --apply

**Duplicate team names are DISAMBIGUATED on the way in, and they must be.**
`store.comp_loadouts` is keyed by `entry.name`, so two comps sharing a
`teamName` silently overwrite each other. The app's export reuses a name for
every lineup it offers under that theme -- 477 files carry only 244 distinct
names, `Black Humours: Sibyl` alone appears 11 times -- so a plain copy
loaded 360 comps instead of 593 and lost 233 of them without a word.

So a name used by more than one source file is rewritten to carry its class
multiset: `Black Humours: Sibyl [Flagellant+Highwayman+Houndmaster+Sibyl]`.
Names used once are left exactly as the app wrote them. The shape is what
distinguishes these comps, and putting it in the name is the same doctrine as
matching on it -- a name was never the identity.

An unrecognised `heroClass` is reported but does NOT block: the loader drops
one SILENTLY and the comp trains as a smaller party, so it has to be visible
here. `Sibyl` and friends read as unrecognised whenever `DDSIM_MOD_DIRS` is
unset, which is an environment fact rather than a defect in the data -- the
message says so rather than crying wolf.
"""

import argparse
import collections
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_SOURCE = Path(
    r"F:\Personal_Fran\Programas\Softwares\nextjs_projects"
    r"\dd-team-builder\src\data\presetComps")
SOURCE_ENV = "DDSIM_PRESET_COMPS"
TARGET = REPO / "metadata" / "COMPS" / "curated_comps"

# Files the app must NOT overwrite, with the reason each is pinned.
#
# `The_Old_Road.json` is the tutorial party and the ONE comp validated against
# the game's own shipped save (`test_the_comp_json_and_the_shipped_save_agree`).
# The 2026-09-19 export kept its four Highwayman skills and ROTATED them --
# `Open Vein` moved from the first slot to the last -- and the save carries the
# equipped slot order, so the export silently stopped matching ground truth. It
# also feeds `tools/eval_tutorial.py`, the one exactly reproducible ruler here.
PROTECTED = {"The_Old_Road.json"}


def source_dir(explicit=None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.environ.get(SOURCE_ENV) or DEFAULT_SOURCE)


def read_comps(directory: Path):
    """{path: parsed}, plus the files that could not be read."""
    good, bad = {}, []
    for path in sorted(directory.glob("*.json")):
        try:
            good[path] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            bad.append((path, str(exc)))
    return good, bad


def multiset(comp) -> tuple:
    """The comp's SHAPE: its sorted class multiset, empty slots included.

    Empty slots are kept rather than filtered. `The Old Road` is a real
    two-hero comp and its two blanks are part of what it is.
    """
    return tuple(sorted(h.get("heroClass", "") for h in comp.get("heroes", [])))


def validate(comps, class_map):
    """Structural problems worth refusing over, and class names worth naming."""
    problems, unknown = [], collections.Counter()
    for path, comp in comps.items():
        heroes = comp.get("heroes")
        if not comp.get("teamName"):
            problems.append((path.name, "no teamName"))
        if not isinstance(heroes, list) or not heroes:
            problems.append((path.name, "no heroes[]"))
            continue
        if len(heroes) != 4:
            problems.append((path.name, f"{len(heroes)} hero slots, expected 4"))
        for hero in heroes:
            name = hero.get("heroClass", "")
            if name and class_map is not None and name not in class_map:
                unknown[name] += 1
    return problems, unknown


def class_map():
    """The loader's own map, or None when the store cannot be built."""
    try:
        from data.comp_loadout_loader import CompLoadoutLoader
        from data.paths import GAME_DIR
        from data.store_cache import load_store_cached
        from data.paths import CSV_DIR
        store = load_store_cached(CSV_DIR, GAME_DIR)
        return CompLoadoutLoader._hero_class_map(store, Path(GAME_DIR))
    except Exception as exc:                      # no game install, no store
        print(f"  (class names unchecked: {exc})")
        return None


def plan(source: Path, target: Path):
    src, src_bad = read_comps(source)
    dst, dst_bad = read_comps(target)
    src_shapes = collections.Counter(multiset(c) for c in src.values())
    dst_shapes = collections.Counter(multiset(c) for c in dst.values())
    return {
        "src": src, "dst": dst, "src_bad": src_bad, "dst_bad": dst_bad,
        "src_shapes": src_shapes, "dst_shapes": dst_shapes,
        "dropped": sorted(set(dst_shapes) - set(src_shapes)),
        "added": sorted(set(src_shapes) - set(dst_shapes)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", help=f"default ${SOURCE_ENV} or {DEFAULT_SOURCE}")
    ap.add_argument("--apply", action="store_true", help="actually replace the folder")
    ap.add_argument("--allow-drops", action="store_true",
                    help="proceed even though a class multiset here is absent upstream")
    args = ap.parse_args()

    source = source_dir(args.source)
    if not source.is_dir():
        print(f"no source directory: {source}")
        return 1
    print(f"source {source}\ntarget {TARGET}\n")

    p = plan(source, TARGET)
    for path, exc in p["src_bad"]:
        print(f"  UNREADABLE (source): {path.name}: {exc}")
    if p["src_bad"]:
        print("\nrefusing: fix the unreadable source files first")
        return 1

    cmap = class_map()
    problems, unknown = validate(p["src"], cmap)
    print(f"  source files      : {len(p['src'])}  ({len(p['src_shapes'])} distinct shapes)")
    print(f"  currently here    : {len(p['dst'])}  ({len(p['dst_shapes'])} distinct shapes)")
    print(f"  shapes ADDED      : {len(p['added'])}")
    print(f"  shapes DROPPED    : {len(p['dropped'])}")
    dup = collections.Counter(c.get("teamName", "") for c in p["src"].values())
    clash = sum(n for n in dup.values() if n > 1)
    print(f"  distinct teamNames: {len(dup)} for {len(p['src'])} files"
          f"{f'  -- {clash} will be disambiguated by shape' if clash else ''}")

    if unknown:
        print(f"\n  unrecognised heroClass values (the loader drops these SILENTLY,")
        print(f"  and the comp then trains as a smaller party):")
        for name, n in unknown.most_common():
            hint = "  <-- set DDSIM_MOD_DIRS if this is a modded class" \
                   if os.environ.get("DDSIM_MOD_DIRS") is None else ""
            print(f"      {name!r:24s} in {n} comps{hint}")

    if problems:
        print(f"\n  STRUCTURAL PROBLEMS ({len(problems)}):")
        for name, why in problems[:20]:
            print(f"      {name}: {why}")
        print("\nrefusing: fix these first")
        return 1

    if p["dropped"] and not args.allow_drops:
        print(f"\n  REFUSING -- {len(p['dropped'])} class multiset(s) here are absent")
        print("  upstream, so applying would lose a comp that was vouched for:")
        for shape in p["dropped"][:20]:
            names = [c.get("teamName") for path, c in p["dst"].items()
                     if multiset(c) == shape]
            print(f"      {'+'.join(x or '-' for x in shape)}   {names}")
        print("\n  Re-run with --allow-drops if that is intended.")
        return 1

    if not args.apply:
        print("\n  plan only -- re-run with --apply to replace the folder")
        return 0

    for path in TARGET.glob("*.json"):
        if path.name not in PROTECTED:
            path.unlink()
    names = collections.Counter(c.get("teamName", "") for c in p["src"].values())

    def qualified(comp):
        """The name plus its shape, for a teamName the export reused."""
        name = comp.get("teamName", "")
        if names[name] <= 1:
            return name
        return f"{name} [{'+'.join(x for x in multiset(comp) if x) }]"

    # A handful of comps share a teamName AND a shape -- the same four classes
    # built two ways, which is a real thing (the build order is ranks, then
    # skills, then trinkets). Those get a stable ordinal from the sorted file
    # name, so the key is unique without inventing a description of what makes
    # them differ.
    qual = collections.Counter(qualified(c) for c in p["src"].values())
    ordinal = collections.Counter()
    renamed = 0
    kept = 0
    for path in sorted(p["src"]):
        if path.name in PROTECTED and (TARGET / path.name).exists():
            kept += 1
            continue
        comp = p["src"][path]
        name = comp.get("teamName", "")
        final = qualified(comp)
        if qual[final] > 1:
            ordinal[final] += 1
            final = f"{final} #{ordinal[final]}"
        if final != name:
            comp = dict(comp, teamName=final)
            renamed += 1
            (TARGET / path.name).write_text(
                json.dumps(comp, indent=2, ensure_ascii=False) + chr(10),
                encoding="utf-8")
        else:
            shutil.copy2(path, TARGET / path.name)
    if renamed:
        print(f"  disambiguated {renamed} comps whose teamName was reused "
              f"(they would have overwritten each other in the store)")
    if kept:
        print(f"  kept {kept} protected file(s) unchanged: {sorted(PROTECTED)}")
    print(f"\n  replaced: {len(p['src'])} files now in {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
