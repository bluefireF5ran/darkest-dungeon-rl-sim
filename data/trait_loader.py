"""Loader for `shared/trait/trait_library.json` — the real overstress
personalities (7 afflictions, 5 virtues) plus the DLC ones.

**This file was never opened by this project.** `engine/combat_engine.py`'s
`_maybe_act_out` documented the opposite outright — "the real per-personality
act-out tables live in the game binary, not in any data file" — and CLAUDE.md /
ASSUMPTIONS.md listed affliction/virtue personality types as deliberately out of
scope. Both were wrong: the tables ship as data, and they contradict what the
engine was doing on four counts (see CHANGELOG.md for the measured tables):

1. The engine charged EVERY act-out as `bark_stress` (+6 stress to the whole
   party at a flat 33%). Really only ~10% of turns bark — Fearful is 4/38, not
   38/38 — so the simulator was manufacturing roughly 3.3x the real
   affliction stress, on top of the exact mechanism that decides the long band.
2. Virtues did nothing per turn. Really every virtue has a 25%/turn beneficial
   act-out (stress heal self/party, party buff, self heal).
3. Virtues were missing the six +25 resists their trait grants.
4. Affliction stat effects were read from `base.buffs.json` ids matching
   `^affliction(Name)Buff\\d+$`. Those ids exist but the traits do NOT reference
   them (the virtues do) — a Fearful hero really takes -25% damage and the
   engine instead gave it -10 ACC / -8 CRIT / +10 PROT.

The `buff_ids` list is a trait's COMPLETE effect set: the -15 resists and
MAXHP-10 shared by every affliction are entries in each affliction's own list,
not a separate baseline to add on top.
"""

from __future__ import annotations

import json
from pathlib import Path

from .types import TraitActOut, TraitDefinition

# `test` is a shipped placeholder (act-outs are `nothing: 1`, i.e. it never does
# anything) and would otherwise be drawn as a 1-in-8 affliction.
SKIPPED_TRAIT_IDS = frozenset({"test"})


class TraitLoader:
    RELATIVE_PATH = Path("shared") / "trait" / "trait_library.json"
    DLC_GLOB = "dlc/**/*trait_library.json"

    @staticmethod
    def _act_out(raw: dict) -> TraitActOut | None:
        kind = raw.get("id")
        chance = int(raw.get("chance") or 0)
        # Weight 0 rows never fire. `retreat_from_combat` is 0 in every trait in
        # the game, which is why this loader never has to model a retreat.
        if not kind or chance <= 0:
            return None
        data = raw.get("data") or {}
        return TraitActOut(
            kind=str(kind),
            chance=chance,
            number_value=float(data.get("number_value") or 0.0),
            string_value=str(data.get("string_value") or ""),
            valid_hero_class_ids=[str(c) for c in (raw.get("valid_hero_class_ids") or [])],
        )

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        """The Arena PvP trait libraries are binary/encrypted, exactly like
        `dlc/**/arena.entries.trinkets.json` — skipped rather than crashing the
        whole load, the same convention TrinketLoader already uses."""
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-16"):
            try:
                return json.loads(raw.decode(encoding))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return None

    @staticmethod
    def load_all(game_dir: str | Path) -> dict[str, TraitDefinition]:
        game_dir = Path(game_dir)
        paths = [game_dir / TraitLoader.RELATIVE_PATH]
        paths += sorted(game_dir.glob(TraitLoader.DLC_GLOB))

        out: dict[str, TraitDefinition] = {}
        for path in paths:
            if not path.exists():
                continue
            data = TraitLoader._read_json(path)
            if not data:
                continue
            for raw in data.get("traits", []):
                trait_id = raw.get("id")
                overstress = raw.get("overstress_type")
                if not trait_id or trait_id in SKIPPED_TRAIT_IDS:
                    continue
                if overstress not in ("affliction", "virtue"):
                    continue
                # Base wins on id collision, matching the rest of the DLC merge.
                if trait_id in out:
                    continue
                act_outs = [a for a in (TraitLoader._act_out(r)
                                        for r in raw.get("combat_start_turn_act_outs", []))
                            if a is not None]
                # Reactions are PROBABILITIES per situation, not weights in
                # a shared table, so they are kept as a plain {kind: chance}
                # dict rather than going through TraitActOut. Zero-chance rows
                # are dropped because every trait ships all 18 kinds and only
                # a handful carry a real number; `comment_*` rows are dropped
                # because they are voice lines (payload `BarkStress`).
                reactions = {
                    str(r.get("id")): float(r.get("chance") or 0.0)
                    for r in (raw.get("reaction_act_outs") or [])
                    if r.get("id") and float(r.get("chance") or 0.0) > 0.0
                    and not str(r.get("id")).startswith("comment_")
                }
                out[str(trait_id)] = TraitDefinition(
                    id=str(trait_id),
                    overstress_type=str(overstress),
                    buff_ids=[str(b) for b in (raw.get("buff_ids") or [])],
                    act_outs=act_outs,
                    reactions=reactions,
                    is_generated=bool(raw.get("is_generated", True)),
                    generate_chance_modifier=float(raw.get("generate_chance_modifier") or 0.0),
                )
        return out
