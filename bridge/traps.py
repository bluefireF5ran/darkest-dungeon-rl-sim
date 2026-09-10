"""Who steps on the trap. Decides; never clicks.

`bridge/curios.py`'s docstring said this was not coverable -- *"Disarm chance is
the `resistance`/`trap` stat, built from class base plus quirks plus trinkets,
and neither `CombatantState` nor `live_state.py` carries it"*. That is now
false in the only part that mattered: `HeroStats.trap_resist` is parsed off
every class's `.info.darkest`, quirk and trinket buffs carry
`stat_type "resistance" / stat_sub_type "trap"` (33 of them), and a live save
carries the hero's quirks and trinkets. So the stat composes from data
throughout and nothing here is invented.

Fran, 2026-09-02:

    for traps you should choose (click on) the hero with the most trap
    resistance and highest stress (as trap defuse relieves stress, specially if
    the trap disarmament probability is greater than 50)

**The stress half is the game's own file, not a theory.** Every disarmable trap
in `props/trap_definitions.json` ships `success_effects: ["Heal Stress TrapD"]`
-- poison_cloud, spikes, blade_wheel, lurker, and the Crimson Court's
slumbering_vampire. Failing costs `Stress 2` plus a DoT plus a fraction of max
HP. So a disarm is a stress HEAL that a stressed hero should be the one to
collect, and a failure is a stress hit that a fresh hero can absorb -- which is
why the ranking flips on the threshold rather than always preferring the most
stressed.

`spikes_ambush` carries no effects at all and is excluded: it is the ambush
variant, which cannot be disarmed.

## A SCOUTED trap is a choice; an UNSCOUTED one is not

The wiki draws a line this module originally blurred:

    Trap Resist is the chance to dodge unscouted traps. If a trap has been
    scouted, the chance for a hero to disarm it is their Trap Resist + 40%.
    ... Disarming a trap renders it harmless and heals 8 stress for the hero.
    -- https://darkestdungeon.wiki.gg/wiki/Trap

So there are two different mechanics wearing one stat:

* **unscouted** -- the trap fires, Trap Resist is the chance to DODGE it, and
  there is no stress heal and **no hero to pick**. Whoever walked into it is
  whoever walked into it.
* **scouted** -- the trap is visible and offers a disarm at Trap Resist + 40%,
  and success heals 8 stress. This is the click Fran is describing, and it is
  the only case where "which hero" is a question.

`advise_trap` therefore REFUSES on an unscouted trap rather than naming a hero,
because naming one implies a choice the game is not offering.

## The chance is a DECLARED READING, and here is its falsifier

`rules.json` gives `difficulty_trap_base = [0, 0, 0, 0.2, 0.2, 0.4, 0.5]`
indexed by quest difficulty, and `trap_scout_disarm_bonus = 0.4`. The reading
is the shape every resisted roll in this engine uses -- `chance - resist`:

    P(disarm) = trap_resist + scouted * 0.4 - difficulty_trap_base[difficulty]

**Four independent cross-checks now agree with it**, all from the wiki page
against numbers this module reads out of shipped files:

| claim | wiki | derived here |
|---|---|---|
| scouted bonus | +40% | `trap_scout_disarm_bonus` **0.4** |
| Veteran / Champion / Darkest penalty | +20 / +40 / +50% | `difficulty_trap_base[3,5,6]` = **0.2 / 0.4 / 0.5** |
| best trinket pair, Grave Robber | **+55%** (Ancestor's Map + Raider Talisman) | 0.25 + 0.30 = **0.55** |
| disarm stress heal | 8 | `Heal Stress TrapD` |

The trinket row is the strongest of the four: nobody derived +55% from
`base.buffs.json`, and the two buffs sum to it exactly. `tests/bridge/test_traps.py`
pins all four.

**Still declared, because the assembled number has never been read off the
game.** DD draws the disarm percentage on the character sheet and in a purple
icon above the health bar when a trap is approached. One live frame with a
known hero on a known trap either matches this or does not; until then
`DISARM_MEASURED` is False and `advise_trap` says so in the line it prints.

**`disarm_chance` clamps to [0, 1] and the STAT does not.** The wiki is
explicit that Disarm Chance can exceed 100% -- that headroom is what pays for
the Champion penalty, and it is preserved because the difficulty is subtracted
*before* the clamp. What clamps is the final probability, which cannot exceed
certainty. `disarm_margin` is the unclamped quantity.

The RANKING is much less sensitive than the number: the difficulty term is the
same for every hero on the tile, so it cancels out of a comparison entirely.
Even if the formula is wrong, who to send is right.

**That only holds if the comparison is made BEFORE the clamp**, which the first
version of this module got wrong and a test caught: floored at 0, a Crusader's
0.10 and a Flagellant's 0.00 both read 0.00 at the hardest difficulty and tie.
`disarm_margin` is the unclamped quantity everything ranks on; `disarm_chance`
is the clamped one that gets printed and tested against the threshold.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.paths import GAME_DIR

# The buff axis a trinket or quirk moves. `stat_sub_type` carries the real
# axis, exactly as it does for `combat_stat_add` -- keying on `stat_type`
# alone would pool every resistance into one number.
RESIST_STAT, TRAP_SUB = "resistance", "trap"

# **`rule_type "always"` IS unconditional, and reading it as a gate cost four
# of the game's best trap trinkets.** 22 of the 33 trap-resistance buffs carry
# it, including Ancestor's Map, Raider's Talisman, Survival Guide and Drifter's
# Buckle -- so the first version of this module, which skipped any buff with a
# truthy `rule_type`, silently valued every one of them at zero. Fran caught
# the symptom: "trinkets and resolve level also change the trap resistance
# value, so the class isnt the only thing".
UNCONDITIONAL_RULES = frozenset({"", "always"})

# Gates that are decidably FALSE for a hero standing on a trap tile: there is
# no camp, no combat round and no monster. Skipping these is knowledge, not
# caution -- unlike an unknown rule, which is counted and reported.
INACTIVE_OUT_OF_COMBAT = frozenset({"in_camp", "firstroundonly", "monsterType"})

# Gates the BRIDGE can decide because it reads the raid save: the torch level
# and the hero's rank. Both are optional arguments; without them the buff is
# unknown rather than granted, which is the direction `lightbelow` cost 234
# wrongly-paid buffs to learn.
LIGHT_RULES = frozenset({"lightbelow", "lightabove"})

# Each resolve level is +10%, and **the wiki names TRAPS FIRST**:
#
#     Each resolve level increases hero chance to disarm traps and resistances
#     to stun, blight, bleed, move and debuff by 10%.
#     -- https://darkestdungeon.wiki.gg/wiki/Resolve_Level
#
# So the trap bonus is stated on its own rather than extrapolated from the
# resistance rule, which matters because trap resistance is the one resistance
# `CombatantState` does not carry at all -- traps are a dungeon-layer mechanic
# and never reach combat.
#
# **THE SCALE IS THE SAVE'S, 0-6**: `resolve_level_thresholds` ships seven
# entries with the first at 0, and `RosterHero.resolve_level` carries it. A
# resolve-0 hero is the baseline `base_stats` describes, so the bonus is
# `level * 0.10` and NOT `(level - 1) * 0.10`.
#
# Fran's worked example is what settled it, and both ends are shipped numbers:
# "a level 0 leper has 60% but a level 6 leper has 60 base + 10*6% extra so
# 120%". The Leper ships `.stun 60% .move 60%`.
#
# `engine/combat_engine.py` had the subtraction and was corrected to match on
# 2026-09-02; `tests/bridge/test_traps.py` asserts the two modules still agree,
# because one rule living in two files is how a rule drifts.
RESIST_PER_RESOLVE_LEVEL = 0.10

# What a successful disarm heals, per the wiki: "Disarming a trap renders it
# harmless and heals 8 stress for the hero." Every disarmable trap carries the
# effect `Heal Stress TrapD` to deliver it. Reported rather than used in the
# ranking -- the ranking only needs to know the payout EXISTS, and the amount
# is what makes the advice line worth reading.
DISARM_STRESS_HEAL = 8

# Fran's threshold: above this the disarm is likely enough that the stress heal
# is the thing to optimise for; below it, the failure is, so send the hero most
# able to take one. A DECLARED CONVENTION with a named source -- his sentence,
# not a game file.
DISARM_WORTH_TRYING = 0.50

# `rules.json` says the disarm bonus for a SCOUTED trap is 0.4. Whether the
# tile in front of us was scouted is a fact the caller has and this does not.
SCOUT_BONUS_KEY = "trap_scout_disarm_bonus"
DIFFICULTY_KEY = "difficulty_trap_base"

# See the module note. False until somebody reads DD's own hover percentage
# off a live trap tile and compares.
DISARM_MEASURED = False


@lru_cache(maxsize=4)
def _rules(game_dir: str) -> dict:
    try:
        with open(Path(game_dir) / "shared" / "rules.json",
                  encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def difficulty_penalty(difficulty: int, game_dir: str = str(GAME_DIR)) -> float:
    """How much harder this quest's traps are. 0 through Veteran, 0.5 at the
    hardest shipped index."""
    table = _rules(game_dir).get(DIFFICULTY_KEY) or []
    if not table:
        return 0.0
    return float(table[min(max(0, int(difficulty)), len(table) - 1)])


def scout_bonus(game_dir: str = str(GAME_DIR)) -> float:
    return float(_rules(game_dir).get(SCOUT_BONUS_KEY) or 0.0)


@lru_cache(maxsize=8)
def disarmable_traps(game_dir: str = str(GAME_DIR)) -> frozenset:
    """Trap ids that can be disarmed at all.

    A trap with no `success_effects` is the ambush variant -- `spikes_ambush`
    is the shipped one -- and offering a hero for it would be advice about a
    choice the game never presents.
    """
    out = set()
    for path in Path(game_dir).glob("**/trap_definitions.json"):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            continue
        for prop in raw.get("props") or []:
            data = prop.get("default_data") or {}
            if data.get("success_effects"):
                out.add(str(prop.get("name") or ""))
    return frozenset(out - {""})


def _buff_trap_amount(store, buff_id: str) -> float:
    buff = getattr(store, "buffs", {}).get(buff_id)
    if buff is None:
        return 0.0
    if (getattr(buff, "stat_type", "") == RESIST_STAT
            and str(getattr(buff, "stat_sub_type", "")) == TRAP_SUB):
        return float(getattr(buff, "amount", 0.0) or 0.0)
    return 0.0


def rule_active(buff, torch=None, rank=None):
    """Is this buff's condition met on a trap tile? `None` means UNKNOWN.

    Three-valued on purpose. `_is_conditional_rule_active` in the engine
    returns True for a rule it has no branch for -- a deliberate choice about
    which way to be wrong, made where a board is in hand. Out here there is no
    board, so the third answer is the honest one and the caller decides: this
    module skips an unknown and reports how many it skipped.
    """
    rule = str(getattr(buff, "rule_type", "") or "")
    if rule in UNCONDITIONAL_RULES:
        return True
    if rule in INACTIVE_OUT_OF_COMBAT:
        return False
    if rule in LIGHT_RULES:
        if torch is None:
            return None
        threshold = float(getattr(buff, "rule_data_float", 0.0) or 0.0)
        # Both comparisons are STRICT -- the shipped thresholds land on a
        # darkness band edge only under `<` / `>`. See CLAUDE.md.
        return torch < threshold if rule == "lightbelow" else torch > threshold
    if rule == "in_rank":
        if rank is None:
            return None
        ranks = str(getattr(buff, "rule_data_string", "") or "")
        return (str(rank) in ranks) if ranks else None
    return None


def trap_resist(store, hero_class: str, quirks: Iterable[str] = (),
                trinkets: Iterable[str] = (), resolve_level: int = 0,
                torch=None, rank=None, unknown=None) -> float:
    """Class base + resolve level + every trap-resistance buff that is ACTIVE.

    Fran, 2026-09-02, and all three sources are here now:

    * **resolve**, at `RESIST_PER_RESOLVE_LEVEL` per level on the save 0-6
      scale -- see that constant, including where the engine disagrees;
    * **trinkets**, with `rule_type "always"` counted rather than skipped,
      which is what four of the game's best trap trinkets carry;
    * **quirks**, which carry NONE in shipped data (0 of the 33 trap buffs
      sits on one). The path is kept because a mod can add one, and the
      absence is worth a test rather than an assumption.

    `unknown` collects the buff ids whose gate could not be decided, so a
    caller can say so instead of quietly under-counting.
    """
    hero = getattr(store, "heroes", {}).get(hero_class)
    total = float(getattr(getattr(hero, "base_stats", None),
                          "trap_resist", 0.0) or 0.0)
    total += max(0, int(resolve_level or 0)) * RESIST_PER_RESOLVE_LEVEL

    def carried(container, attr):
        for item_id in container or ():
            holder = getattr(store, attr, {}).get(item_id)
            for buff_id in (getattr(holder, "buffs", None) or ()):
                yield buff_id

    for buff_id in (list(carried(quirks, "quirks"))
                    + list(carried(trinkets, "trinkets"))):
        amount = _buff_trap_amount(store, buff_id)
        if not amount:
            continue
        active = rule_active(getattr(store, "buffs", {}).get(buff_id), torch, rank)
        if active is None:
            if unknown is not None:
                unknown.append(buff_id)
            continue
        if active:
            total += amount
    return total


def disarm_margin(store, hero_class: str, quirks: Iterable[str] = (),
                  trinkets: Iterable[str] = (), difficulty: int = 1,
                  scouted: bool = False, game_dir: str = str(GAME_DIR),
                  resolve_level: int = 0, torch=None, rank=None) -> float:
    """The UNCLAMPED `resist + scout - difficulty`. What to RANK on.

    **The clamp is what breaks the invariance argument**, and a test caught it:
    `disarm_chance` floors at 0, so at the hardest difficulty a Crusader's 0.10
    and a Flagellant's 0.00 both become 0.00 and tie. The ordering between them
    then falls to whatever the sort does with equal keys, which is exactly the
    arbitrariness the module claims not to have.

    Ranking on the margin keeps the claim true: the difficulty and scout terms
    are identical for every hero on the tile, so they cancel out of any
    comparison and the order is decided by the stat alone. `disarm_chance` is
    still what gets REPORTED and what the threshold is tested against, because
    a negative probability is not a thing to print.
    """
    margin = trap_resist(store, hero_class, quirks, trinkets,
                         resolve_level, torch, rank)
    if scouted:
        margin += scout_bonus(game_dir)
    return margin - difficulty_penalty(difficulty, game_dir)


def disarm_chance(store, hero_class: str, quirks: Iterable[str] = (),
                  trinkets: Iterable[str] = (), difficulty: int = 1,
                  scouted: bool = False, game_dir: str = str(GAME_DIR),
                  resolve_level: int = 0, torch=None, rank=None) -> float:
    """P(this hero disarms it). Clamped to [0, 1]. See the module note."""
    return max(0.0, min(1.0, disarm_margin(store, hero_class, quirks, trinkets,
                                           difficulty, scouted, game_dir,
                                           resolve_level, torch, rank)))


@dataclass(frozen=True)
class TrapCandidate:
    hero_id: str
    hero_class: str
    chance: float             # clamped, for reporting and the threshold
    stress: float
    hp_fraction: float
    margin: float = 0.0       # unclamped, for RANKING -- see `disarm_margin`


@dataclass(frozen=True)
class TrapAdvice:
    trap_id: str
    area_id: str
    hero: Optional[str]
    hero_class: str
    chance: float
    criterion: str
    why: str
    candidates: tuple[TrapCandidate, ...] = ()

    def line(self) -> str:
        if self.hero is None:
            return f"{self.area_id:6s} {self.trap_id}: {self.why}"
        note = "" if DISARM_MEASURED else " (unverified formula)"
        return (f"{self.area_id:6s} {self.trap_id} -> {self.hero_class} "
                f"[{self.hero}]  {self.chance:.0%}{note}  - {self.why}")


def advise_trap(store, trap_id: str, party: Sequence, area_id: str = "",
                difficulty: int = 1, scouted: bool = False,
                game_dir: str = str(GAME_DIR),
                threshold: float = DISARM_WORTH_TRYING,
                torch=None) -> TrapAdvice:
    """Who to send at this trap, and why.

    `party` is anything with `hero_class`, `stress`, and optionally `quirks`,
    `trinkets`, `hp_fraction` -- `curios.CurioHero` and `live_state.LiveHero`
    both qualify.

    **Two regimes, and the threshold is what separates them:**

    * a disarm is likely -> send the **most stressed** hero who clears the
      threshold, because `Heal Stress TrapD` is the payout and it should land
      where it is worth most;
    * nobody clears it -> the payout is unlikely and the FAILURE is what to
      plan for, so send the hero most able to absorb `Stress 2` plus a DoT
      plus a fraction of max HP: the best chance first, then the freshest.

    The difficulty term is identical for every hero on the tile, so it cancels
    out of the comparison -- which is why the ranking is trustworthy even
    while `DISARM_MEASURED` is False.
    """
    if trap_id and trap_id not in disarmable_traps(game_dir):
        return TrapAdvice(trap_id=trap_id, area_id=area_id, hero=None,
                          hero_class="", chance=0.0, criterion="none",
                          why="this trap cannot be disarmed (an ambush variant)")
    if not party:
        return TrapAdvice(trap_id=trap_id, area_id=area_id, hero=None,
                          hero_class="", chance=0.0, criterion="none",
                          why="no party")
    if not scouted:
        # **There is no click here.** An unscouted trap fires on whoever walks
        # into it; Trap Resist is their chance to DODGE, not to disarm, and
        # nothing heals stress. Naming a hero would imply a choice the game is
        # not offering. See the module docstring.
        return TrapAdvice(trap_id=trap_id, area_id=area_id, hero=None,
                          hero_class="", chance=0.0, criterion="none",
                          why="not scouted: it fires on whoever walks into it, "
                              "and Trap Resist is a dodge rather than a disarm")

    candidates = []
    for hero in party:
        hero_class = str(getattr(hero, "hero_class", ""))
        quirks = tuple(getattr(hero, "quirks", ()) or ())
        trinkets = tuple(getattr(hero, "trinkets", ()) or ())
        margin = disarm_margin(store, hero_class, quirks, trinkets,
                               difficulty, scouted, game_dir,
                               int(getattr(hero, "resolve_level", 0) or 0),
                               torch, getattr(hero, "position", None))
        chance = max(0.0, min(1.0, margin))
        candidates.append(TrapCandidate(
            hero_id=str(getattr(hero, "id", None)
                        or getattr(hero, "guid", "")
                        or getattr(hero, "hero_class", "")),
            hero_class=str(getattr(hero, "hero_class", "")),
            chance=chance, margin=margin,
            stress=float(getattr(hero, "stress", 0.0) or 0.0),
            hp_fraction=float(getattr(hero, "hp_fraction", 1.0) or 1.0)))

    able = [c for c in candidates if c.chance >= threshold]
    if able:
        pick = max(able, key=lambda c: (c.stress, c.margin))
        why = (f"{len(able)} of {len(candidates)} clear {threshold:.0%}; "
               f"the disarm heals {DISARM_STRESS_HEAL} stress, so it goes to "
               f"the most stressed")
        criterion = "most stressed above the threshold"
    else:
        pick = max(candidates, key=lambda c: (c.margin, c.hp_fraction, -c.stress))
        why = (f"nobody clears {threshold:.0%} (best {pick.chance:.0%}); "
               f"planning for the failure instead -- best odds, then freshest")
        criterion = "best odds, then most able to eat the failure"

    return TrapAdvice(trap_id=trap_id, area_id=area_id, hero=pick.hero_id,
                      hero_class=pick.hero_class, chance=pick.chance,
                      criterion=criterion, why=why,
                      candidates=tuple(sorted(candidates,
                                              key=lambda c: -c.margin)))


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--difficulty", type=int, default=1)
    ap.add_argument("--scouted", action="store_true")
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    args = ap.parse_args()

    from data.store_cache import load_store_cached

    store = load_store_cached(str(Path(__file__).resolve().parent.parent),
                              args.game_dir)
    print(f"disarmable traps: {sorted(disarmable_traps(args.game_dir))}")
    print(f"difficulty_trap_base: {_rules(args.game_dir).get(DIFFICULTY_KEY)}")
    print(f"scout bonus: {scout_bonus(args.game_dir)}")
    print(f"\ntrap resistance by class, difficulty {args.difficulty}"
          f"{' (scouted)' if args.scouted else ''}:")
    rows = []
    for hero_class in sorted(getattr(store, "heroes", {})):
        rows.append((disarm_chance(store, hero_class, (), (), args.difficulty,
                                   args.scouted, args.game_dir), hero_class))
    for chance, hero_class in sorted(rows, reverse=True):
        flag = "  <- clears the threshold" if chance >= DISARM_WORTH_TRYING else ""
        print(f"  {hero_class:16s} {chance:6.0%}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
