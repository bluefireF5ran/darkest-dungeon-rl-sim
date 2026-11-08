"""Every key the monster files carry, against every key the code reads.

**Why this exists.** On 2026-09-19 three gaps of one shape were found in a
single session, each a field the game ships, the HERO side reads, and the
monster side did not: `.launch` (228 of 694 enemy skills usable from ranks the
game forbids), `.move` (101 of 552 skill lines with a self-move nothing
applied), and `shared_health` (The Flesh, four bars where the game has one).

The first two were found by reading a community guide and noticing the
simulator disagreed. The third was found by chasing a boss write-up. **None of
them was found by the audit that was supposed to list exactly this**, because
that audit lives in prose in `ASSUMPTIONS.md` and prose does not re-run. Every
other unread monster key was counted there; `shared_health` never appeared,
and nothing noticed for the life of the project.

So: the list is code now, and `tests/suite/test_monster_key_audit.py` fails
when a key turns up that nobody has classified.

    python tools/monster_key_audit.py            # undeclared keys, if any
    python tools/monster_key_audit.py --all      # the whole table
    python tools/monster_key_audit.py --carriers does_count_towards_stall_penalty

**What "read" means here, and its one weakness.** A key counts as read when
its field name appears anywhere in `data/`, `engine/` or `env/`. That is
deliberately generous: it cannot produce a false alarm, only miss one. A field
whose name is mentioned but whose VALUE is never consulted still reads as
handled -- which is precisely how `.launch` hid, since `data/` parsed it
faithfully and only `_monster_ai.py` failed to consult it. **This tool finds
keys nobody has looked at; it cannot find keys someone parsed and then
ignored.** For that, the instrument is `tools/pizzarat_oracle.py`, which
grades behaviour against an independent transcription.
"""

import argparse
import collections
import glob
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

RECORD = re.compile(r"^([a-z_]+):\s*(.*)$")
FIELD = re.compile(r"\.([a-z_][a-z_0-9]*)")     # leading letter: skips `.0875`

# (record, field) or a bare record name -> why nobody needs to read it.
# A key that is NOT here and NOT read turns the audit red. Adding a line is
# the whole workflow: classify it, say why, and the suite goes quiet again.
DECLARED = {
    # -- already-declared exclusions in ASSUMPTIONS.md ------------------------
    "captor_empty": "the Hag/Siren capture mechanic, a declared out-of-scope exclusion",
    "captor_full": "the Hag/Siren capture mechanic, a declared out-of-scope exclusion",
    "shape_shifter": "declared new subsystem (12 records, the Flesh's body-part reroll)",
    "companion": "declared new subsystem (`heal_per_turn_percent`, 6 records)",
    "controller": "declared new subsystem (the Siren's charm, 3 records)",
    ("shared_health", "id"):
        "UNMODELLED and queued -- The Flesh's one pool against the simulator's "
        "four bars, ~4x too durable. ASSUMPTIONS.md and NEXT.md item 1b",

    # -- corpse semantics, handled structurally rather than by flag ----------
    # All six carry ONLY on `corpse` and `corpse_large`, and the engine models
    # a corpse as its own thing: `is_alive()` is False for one, so it is
    # already out of the stall count, the random-target pool and the guard
    # pool without anyone reading the flag that says so.
    ("battle_modifier", "does_count_towards_stall_penalty"):
        "corpse-only; `effective_enemy_stall_size` filters to is_alive()",
    ("battle_modifier", "can_be_random_target"): "corpse-only; corpses are not in the target pool",
    ("battle_modifier", "is_valid_friendly_target"): "corpse-only",
    ("battle_modifier", "can_be_summon_rank"): "corpse-only",
    ("battle_modifier", "does_count_as_monster_size_for_monster_brain"): "corpse-only",
    ("battle_modifier", "does_count_as_guardable_for_monster_brain"): "corpse-only",
    ("battle_modifier", "can_relieve_stress_from_killing_blow"): "corpse-only",

    # -- subsumed by something already validated against the real game -------
    ("personality", "prefskill"):
        "inert on 227 of 254 monsters (-1), and all 21 non-inert carriers have "
        "brain skill_desires -- selection is governed by the brain, which "
        "`tools/brain_oracle.py` grades 46/46 against the game's own weights",

    # -- boss-only, and small ------------------------------------------------
    ("battle_modifier", "disabled_act_out_combat_start_turn_types"):
        "boss-only (13 monsters): suppresses some affliction act-outs at "
        "combat start in boss fights. Act-outs are modelled; this narrowing is not",
    ("battle_modifier", "living_other_enemy_buffs"): "boss-only, 3 records",
    ("battle_modifier", "living_hero_buff_instance_ids"): "boss-only, 3 records",
    ("battle_modifier", "can_be_hit"): "1 record; `can_be_damaged_directly` is the one that is read",
    ("skill", "is_knowledgeable"):
        "the Ancestor forms and the Collector -- Darkest Dungeon and roaming "
        "bosses, both declared absent entirely",
    "death_damage": "1 record, a boss phase transition",
    "skill_reaction": "2 records, boss reaction scripting",

    # -- presentation, loot bookkeeping, and other non-combat ----------------
    "battle_backdrop": "which art the fight is drawn on",
    ("initiative", "hide_indicator"): "whether the turn order shows the icon",
    ("skill", "can_miss"): "presentation flag; the hit roll is graded by tools/hit_oracle.py",
    ("skill", "is_user_selected_targets"): "hero-facing targeting UI",
    ("skill", "is_monster_rerank_valid_on_friendly_presentation_end"): "animation sequencing",
    ("additional_effect", "is_valid_trinket_target"): "trinket targeting metadata",
    ("additional_effect", "is_valid_trinket_attacker"): "trinket targeting metadata",
    ("life_link", "does_spawn_loot"): "loot bookkeeping, not combat",
    ("life_time", "does_check_for_loot"): "loot bookkeeping, not combat",
    ("death_class", "is_valid_on_blight_dot"): "phase-transition gating; declared",
    ("death_class", "is_valid_on_burn_dot"): "phase-transition gating; declared",
    ("death_class", "can_die_from_damage"): "phase-transition gating; declared",
    ("death_class", "carry_over_hp_min_percent"): "phase-transition gating; declared",
    ("death_class", "change_class_effects"): "phase-transition gating; declared",
}


def game_dir():
    from data.paths import GAME_DIR
    return Path(GAME_DIR)


def scan_keys(monsters_dir):
    """{(record, field): count} over every shipped monster file."""
    out = collections.Counter()
    pattern = os.path.join(str(monsters_dir), "*", "*", "*.info.darkest")
    for path in glob.glob(pattern):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = RECORD.match(line.strip())
                if not m:
                    continue
                for field in FIELD.findall(m.group(2)):
                    out[(m.group(1), field)] += 1
    return out


def carriers(monsters_dir, field):
    """Which monster FAMILIES carry a field -- the question that classifies it.

    Six of the flags in DECLARED collapsed to one line each the moment this
    said "corpse, corpse_large".
    """
    out = set()
    pattern = os.path.join(str(monsters_dir), "*", "*", "*.info.darkest")
    for path in glob.glob(pattern):
        with open(path, encoding="utf-8", errors="replace") as fh:
            if re.search(r"\." + re.escape(field) + r"\b", fh.read()):
                out.add(Path(path).parts[-3])
    return sorted(out)


def read_by_code():
    """Every identifier-ish token the policy's own source mentions."""
    blob = []
    for sub in ("data", "engine", "env"):
        for path in glob.glob(str(REPO / sub / "**" / "*.py"), recursive=True):
            blob.append(Path(path).read_text(encoding="utf-8", errors="replace"))
    return "\n".join(blob)


def audit(show_all=False):
    monsters = game_dir() / "monsters"
    if not monsters.is_dir():
        print(f"no monster files at {monsters}")
        return 1
    keys = scan_keys(monsters)
    source = read_by_code()

    undeclared, declared_unread, read = [], [], 0
    for (record, field), n in keys.items():
        if field in source:
            read += 1
            continue
        why = DECLARED.get((record, field)) or DECLARED.get(record)
        (declared_unread if why else undeclared).append((record, field, n, why))

    print(f"{len(keys)} (record, field) pairs over {len(set(r for r, _ in keys))} "
          f"record types")
    print(f"  read by data/ engine/ env/ : {read}")
    print(f"  unread but declared        : {len(declared_unread)}")
    print(f"  UNDECLARED                 : {len(undeclared)}")

    if show_all:
        print("\ndeclared-unread:")
        for record, field, n, why in sorted(declared_unread, key=lambda r: (-r[2], r[0])):
            print(f"  {record}.{field}  ({n})\n      {why}")

    if undeclared:
        print("\nUNDECLARED KEYS -- classify each one in DECLARED, or fix the gap:")
        for record, field, n in sorted(((r, f, n) for r, f, n, _ in undeclared),
                                       key=lambda r: -r[2]):
            fams = carriers(monsters, field)
            shown = ", ".join(fams[:6]) + (" ..." if len(fams) > 6 else "")
            print(f"  {record}.{field:44s} {n:4d} records   carried by: {shown}")
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true", help="print the declared-unread table too")
    ap.add_argument("--carriers", metavar="FIELD",
                    help="which monster families carry this field")
    args = ap.parse_args()
    if args.carriers:
        for fam in carriers(game_dir() / "monsters", args.carriers):
            print(" ", fam)
        return 0
    return audit(args.all)


if __name__ == "__main__":
    raise SystemExit(main())
