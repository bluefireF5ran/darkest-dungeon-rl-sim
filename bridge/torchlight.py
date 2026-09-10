"""Torch policy — PLAYBOOK 2.3, including the shutdown.

Hold the light at radiant; then, on the last turn of a fight, before the blow
that ends it, drop the torch to nothing so the corpses pay better. This is the
one dungeon decision that is purely economic — it buys loot with risk that has
already been spent, because by then the fight is won.

## It turns out to be real data, not a model that had to be invented

`shared/rules.json`'s `darkness.range_table` carries **five brackets**, and it
prices darkness in both directions at once — what it costs and what it pays:

| torch | gold | treasure draws | monster atk / dmg / crit | stress chance / dmg | scouting | surprise |
|---|---|---|---|---|---|---|
| 76-100 | — | — | — | — | +15 | monsters +25 |
| 51-75 | — | — | +0 / +0 / +1 | +5 / +10 | +7.5 | monsters +15 |
| 26-50 | **+10%** | **+4** | +5 / +10 / +2 | +10 / +20 | — | heroes +15 |
| 1-25 | **+15%** | **+8** | +10 / +15 / +3 | +25 / +30 | — | heroes +25 |
| 0 and below | **+30%** | **+12** | +12.5 / +25 / +5 | +35 / +40 | — | heroes +40 |

Those loot columns were being parsed and thrown away — `DarknessBand` dropped
them as "dungeon mechanics with no analog in this simulator". They are kept now,
read here and still never applied by the engine, so nothing about combat changed.

## The brackets exclude their LOWER bound, and the engine had it backwards

`darkness.lowest_excluded` is `true`, which makes the tiers 76-100 / 51-75 /
26-50 / 0-25 — the same list `CombatEngine._get_darkness_band` cited in its own
docstring while implementing `lower <= torch < upper`, i.e. putting 75 in the
radiant band. Fixed there.

It was inert for training and every archived eval (they field torch 100, and the
two readings differ only at exactly 75 / 50 / 25 / 0), and **not** inert for this
bridge: `seed.py` passes the real torch out of the save, and PLAYBOOK 2.3 parks
it at exactly 75 on purpose. Under the old reading the advisor simulated a
radiant fight while the game ran a dim one.

## What the shutdown costs

**Nothing in any shipped file states how much light one torch restores.** It is
not in `inventory/base.supply.inventory.items.darkest` (which gives the torch a
stack limit and a price and no effect), not in `base.effects.darkest` (whose
`torch_increase` entries belong to curios and hero skills), and not under
`raid/`. `TORCH_RESTORE` below is therefore **an expert-measured constant, not
game data**: 4 torches take a dark run back to full, so 25 each.

**And the cost is only ever paid on a fight that has not happened yet.** The
first version of this priced every shutdown in torches, which refused the play
outright for a party carrying none -- including the tutorial party, which the
game hands no torches at all. `battles_left == 0` is the repair: with no fight
after this one the light is never bought back, so the pack does not enter it.
What the walk home really costs is `corridor_return_content` rolls at the
raised dark rate, counted in `bridge/lootplan.py` where the route is known.

Three cases where the shutdown is free, all from real rules:

- **The dungeon's last fight.** There is nothing left to walk through, so the
  light never has to be bought back. PLAYBOOK 2.3 says always do it here.
- **Any fight with no fight after it**, for the same reason one step weaker:
  the walk home still happens, but no torch is ever spent on it.
- **Immediately before a campfire.** `camp_restore_torch` is **100** — camping
  refills the torch outright, so a shutdown on the fight before a camp costs
  zero torches. PLAYBOOK 2.3 does not mention this one.

And one case that is not a choice: `ambush_torch_reduction` is **-100**, so a
camp ambush zeroes the light for you.

**The module is `torchlight.py`, not `torch.py`, and that is not cosmetic.**
Running any script inside `bridge/` puts that directory at the front of
`sys.path`, so a `bridge/torch.py` shadows the real `torch` package — which
broke `bridge/retreat.py` the moment it imported the checkpoint
(`ModuleNotFoundError: 'torch' is not a package`). Same trap `data/types.py`
carries against the stdlib `types`. `torchlight` is the save's own field name
anyway.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.types import CombatRules, DarknessBand

# Light restored by one torch. NOT from a shipped file — see the module
# docstring. Measured: 4 torches take 0 back to 100.
TORCH_RESTORE = 25.0

# PLAYBOOK 2.3's working level. The data agrees it is the right default: the
# brightest bracket is the only one with no stress penalty and no monster
# bonus, and it is the only one that still scouts (+15) and surprises monsters
# (+25).
RADIANT_FLOOR = 76.0

# What the shutdown aims at. Zero is the bottom bracket — +30% gold and +12
# treasure draws — and is where shift-clicking the torch bar lands.
SHUTDOWN_LEVEL = 0.0


def band_for(rules: CombatRules, torch: float) -> Optional[DarknessBand]:
    """The bracket a torch level falls in, lower bound EXCLUDED.

    Mirrors `CombatEngine._get_darkness_band`; both follow
    `darkness.lowest_excluded`.
    """
    for band in rules.darkness_table:
        if band.lower < torch <= band.upper:
            return band
    return None


def torches_to_restore(torch: float, target: float = 100.0) -> int:
    if torch >= target:
        return 0
    return int(math.ceil((target - torch) / TORCH_RESTORE))


@dataclass(frozen=True)
class TorchAdvice:
    torch: float
    bracket: str
    action: str                    # "shutdown" | "relight" | "hold"
    reason: str
    gold_bonus: float = 0.0
    extra_draws: float = 0.0
    torches_needed: int = 0
    torches_held: int = 0
    free_because: str = ""

    def line(self) -> str:
        head = {
            "shutdown": "SHUT DOWN the torch before the killing blow",
            "relight": "RELIGHT",
            "hold": "hold the light",
        }[self.action]
        return f"torch {self.torch:.0f} ({self.bracket}) — {head}: {self.reason}"

    def report(self) -> str:
        out = [self.line()]
        if self.action == "shutdown":
            out.append(f"  pays +{self.gold_bonus:.0f}% gold and +{self.extra_draws:.0f} "
                       f"treasure draws on this fight's loot")
            if self.free_because:
                out.append(f"  costs nothing: {self.free_because}")
            else:
                out.append(f"  costs {self.torches_needed} torches to relight "
                           f"(you hold {self.torches_held})")
        return "\n".join(out)


def describe(rules: CombatRules, torch: float) -> str:
    band = band_for(rules, torch)
    if band is None:
        return "unknown"
    if band.lower >= RADIANT_FLOOR - 1:
        return "radiant"
    if band.loot_increase_gold <= 0:
        return "dim"
    if band.loot_increase_treasure_draws >= 12:
        return "pitch black"
    return "dark" if band.loot_increase_treasure_draws <= 4 else "black"


def advise_torch(rules: CombatRules, torch: float, torches_held: int,
                 last_combat_of_run: bool = False,
                 camp_next: bool = False,
                 about_to_win: bool = True,
                 battles_left: Optional[int] = None) -> TorchAdvice:
    """Shut the torch down here, or keep the light?

    `about_to_win` is the whole safety argument. Darkness hands the monsters
    +12.5 accuracy, +25% damage and +5 crit, so the shutdown is only ever a free
    lunch on the turn that ends the fight — before that it is a bet, and this
    module refuses to recommend it.
    """
    bottom = rules.darkness_table[-1] if rules.darkness_table else None
    bracket = describe(rules, torch)
    if bottom is None:
        return TorchAdvice(torch=torch, bracket=bracket, action="hold",
                           reason="no darkness table loaded")

    here = band_for(rules, torch)
    gain_gold = bottom.loot_increase_gold - (here.loot_increase_gold if here else 0.0)
    gain_draws = (bottom.loot_increase_treasure_draws
                  - (here.loot_increase_treasure_draws if here else 0.0))

    # The cost of a shutdown is getting back to WHERE YOU WERE, not to 100.
    # A party already down at 25 pays one torch to undo it, not four; charging
    # the full relight would talk it out of a play that is nearly free.
    needed = torches_to_restore(SHUTDOWN_LEVEL, torch)

    free_because = ""
    if last_combat_of_run:
        free_because = "the run's last fight — there is nothing left to walk through"
    elif battles_left == 0:
        # **The torch budget is not what a shutdown costs when no fight
        # remains.** A relight buys light for a FIGHT; with none left there is
        # nothing to buy, so the pack is irrelevant and a party holding zero
        # torches may still snuff. That is PLAYBOOK 2.3's "en el último
        # combate de la mazmorra siempre compensa", and it is the case the
        # tutorial is made of -- the Old Road hands out no torches at all, so
        # pricing the shutdown in torches refused the one play the run is for.
        #
        # The walk home is not free, and it is not free in torches either: it
        # costs `corridor_return_content` rolls at the raised dark rate, which
        # `bridge/lootplan.py` counts and reports. Charging it here as a torch
        # price was the wrong currency for a real cost.
        free_because = ("no fight left after this one — the light would never "
                        "be bought back, whatever the pack holds")
    elif camp_next:
        free_because = (f"a campfire is next and it restores the torch to "
                        f"{rules.camp_restore_torch:.0f}")

    if gain_gold <= 0 and gain_draws <= 0:
        return TorchAdvice(
            torch=torch, bracket=bracket, action="hold",
            reason="already in the darkest bracket — there is no more loot to buy",
            gold_bonus=bottom.loot_increase_gold,
            extra_draws=bottom.loot_increase_treasure_draws,
            torches_held=torches_held)

    if not about_to_win:
        below_radiant = torch < RADIANT_FLOOR
        if below_radiant and torches_held > 0 and not (last_combat_of_run or camp_next):
            return TorchAdvice(
                torch=torch, bracket=bracket, action="relight",
                reason=("dark outside the killing blow is a straight loss — monsters "
                        f"gain +{here.monster_attack_increase:.0f} accuracy, "
                        f"+{here.monster_damage_increase:.0f}% damage and "
                        f"+{here.monster_crit_increase:.0f} crit for loot you have not "
                        "earned yet" if here else "darkness costs more than it pays here"),
                torches_needed=torches_to_restore(torch), torches_held=torches_held)
        return TorchAdvice(
            torch=torch, bracket=bracket, action="hold",
            reason="the fight is not won yet; darkness is only free on the turn that ends it",
            torches_held=torches_held)

    if not free_because and torches_held < needed:
        return TorchAdvice(
            torch=torch, bracket=bracket, action="hold",
            reason=(f"getting back to {torch:.0f} would cost {needed} "
                    f"torch{'es' if needed != 1 else ''} and you hold {torches_held}"),
            gold_bonus=gain_gold, extra_draws=gain_draws,
            torches_needed=needed, torches_held=torches_held)

    return TorchAdvice(
        torch=torch, bracket=bracket, action="shutdown",
        reason=(free_because or
                f"{needed} torch{'es' if needed != 1 else ''} buys +{gain_gold:.0f}% gold "
                f"and +{gain_draws:.0f} treasure draws on this fight"),
        gold_bonus=gain_gold, extra_draws=gain_draws,
        torches_needed=0 if free_because else needed,
        torches_held=torches_held, free_because=free_because)


def advise_from_live(state, rules: CombatRules, last_combat_of_run: bool = False,
                     camp_next: bool = False, about_to_win: bool = True,
                     moves_left: Optional[int] = None,
                     battles_left: Optional[int] = None) -> TorchAdvice:
    """Torch advice straight off a `LiveState`.

    `moves_left` is how many corridors the route still has to cross. It is
    required to claim the shutdown is **free**, and this used to infer that from
    `remaining_battles <= 1` alone — which is a different statement. Caught in
    the first live run: with one fight but **five moves and eight unexplored
    areas** left, the advice read "the run's last fight — there is nothing left
    to walk through" and priced the relight at 0 torches. Walking five corridors
    pitch black is not free; it costs scouting, and it raises the ambush chance
    on every one of them.

    Unknown (`None`) does not infer it either. Without the route this module
    cannot tell the two apart, and the honest fallback is to charge the relight
    — that still recommends the shutdown whenever the torches are in the pack,
    just with its real price on it."""
    held = int((state.provisions or {}).get("torch", 0) or 0)
    remaining = state.remaining_battles
    if remaining is not None and remaining <= 1 and moves_left == 0:
        last_combat_of_run = True
    # **`battles_left` is NOT derived from `remaining_battles` here**, and the
    # difference is a bug this module already paid for once. That counter is
    # the fights the save KNOWS about; an unexplored area can hold more, so
    # "one fight left" is not "no fight after this one" -- which is the same
    # confusion that once printed "the run's last fight" with five moves and
    # eight unexplored areas still to walk. Only a caller holding the map can
    # answer it, and `bridge/lootplan.py` is the one that does.
    return advise_torch(rules, float(state.torchlight or 0.0), held,
                        last_combat_of_run=last_combat_of_run,
                        camp_next=camp_next, about_to_win=about_to_win,
                        battles_left=battles_left)


def main() -> None:
    import argparse
    from bridge.live_state import read_live_state
    from data.rules import RulesLoader

    ap = argparse.ArgumentParser(description="shut the torch down, or keep the light?")
    ap.add_argument("--raid", required=True, help="a persist.raid.json (a COPY)")
    ap.add_argument("--roster", default=None)
    ap.add_argument("--last", action="store_true", help="this is the run's last fight")
    ap.add_argument("--camp-next", action="store_true")
    ap.add_argument("--not-won", action="store_true",
                    help="the fight is NOT about to end this turn")
    ap.add_argument("--table", action="store_true", help="print the whole darkness table")
    args = ap.parse_args()

    from data.paths import GAME_DIR as game_dir
    rules = RulesLoader.load(game_dir / "shared" / "rules.json")

    if args.table:
        print(f"{'bracket':>16}  {'gold':>6} {'draws':>6}  {'m.atk':>6} {'m.dmg':>6} "
              f"{'m.crit':>6}  {'scout':>6}")
        for band in rules.darkness_table:
            upper = "100+" if band.upper > 1e30 else f"{band.upper:.0f}"
            lower = "-inf" if band.lower < -1e30 else f"{band.lower:.0f}"
            print(f"{lower:>7}-{upper:<8}  {band.loot_increase_gold:>5.0f}% "
                  f"{band.loot_increase_treasure_draws:>6.0f}  "
                  f"{band.monster_attack_increase:>6.0f} {band.monster_damage_increase:>6.0f} "
                  f"{band.monster_crit_increase:>6.0f}  {band.player_scouting_increase:>6.1f}")
        print()

    roster = args.roster or args.raid.replace("raid.json", "roster.json")
    state = read_live_state(raid_file=args.raid, roster_file=roster)
    advice = advise_from_live(state, rules, last_combat_of_run=args.last,
                              camp_next=args.camp_next, about_to_win=not args.not_won)
    print(advice.report())


if __name__ == "__main__":
    main()
