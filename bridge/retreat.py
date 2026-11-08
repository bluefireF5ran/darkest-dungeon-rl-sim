"""Retreat policy — PLAYBOOK 2.6, priced in the currency the decision is
actually made in.

`advisor.py --survival` prints "P(finish intact) 31% — considerar retirada" and
recommends pulling out below a half. That threshold was never mis-calibrated; it
was **mis-framed**. PLAYBOOK 2.6 does not compare a probability to a number. It
compares two bills:

> *"reparar el estrés de los cuatro cuesta más que reparar el de uno solo más la
> pérdida de oportunidad de esa mazmorra"*

So this module puts both sides in gold and subtracts. Every input is real game
data except where marked.

## The exchange rates, and why they settle the question so decisively

| what | gold | source |
|---|---|---|
| one point of stress | **22.2** un-upgraded Abbey, **10.0** fully upgraded | `abbey.building.json`: 1000/850/700 gold against 45/56/70 healed |
| a whole party retreating | 20 stress x 4 = **~1,776** | `rules.json` `combat_retreat_stress` = 20 |
| a dead rank-4 hero | **~36,000** | `upgrades/heroes/<class>.upgrades.json`, summed over the ranks bought |
| a generic quest's reward | **~3,000** plus heirlooms and a trinket | `raid.raid_instance.completion_reward` |

A death costs roughly **twenty times** a full-party retreat and **twelve times**
the quest it is being risked for. That is the whole of PLAYBOOK 2.6's second
clause, and it explains the shape of expert play the probability framing could
not: retreat is *rare*, because stress is cheap — and when it happens it is
*decisive*, because a hero is not.

A fresh recruit is free from the stage coach (PLAYBOOK 0.1), so what dies is not
"a hero" but the gold sunk into their weapon, armour and skill ranks. That is
what the upgrade trees add up to, and it is why losing a level-0 costs nothing
and losing a level-5 ends a week.

## What is estimated rather than known

The chance of dying and the stress still to be taken come from
`bridge/rollout.py`, which is **a lower bound**: it is the probability *the
policy* finishes intact, and the policy plays worse than the human reading this.
It ranks options; it is not odds. Everything else here is arithmetic on shipped
numbers.

Two things are deliberately **not** priced: the heirlooms and the trinket a
forfeited quest would have paid. Heirlooms have no gold price (they buy building
upgrades), and PLAYBOOK 1.2 values a trinket by whether any of our comps equips
it, not in coin. Both are reported beside the total instead of being folded into
it with an invented exchange rate.
"""

from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.town_costs import gold_per_stress, hero_investment
from data.dson import parse_file

from data.paths import GAME_DIR

# rules.json `combat_retreat_stress`. Read at call time rather than hardcoded
# where the rules are available; this is the fallback for a caller with none.
RETREAT_STRESS = 20.0

# --- the survival floor -------------------------------------------------
#
# The gold arithmetic decides the ordinary case and it is right to: a fresh
# recruit genuinely IS worth risking for a rich quest, which is the whole of
# PLAYBOOK 0.1's dual. But it is an EXPECTED-VALUE rule, and expected value is
# the wrong instrument for a decision you only get to make once with a hero
# who does not come back.
#
# Fran, 2026-09-03, on watching a party keep going at `P(finish intact) 0%`:
# an afflicted hero is "almost the same as a death". So a hard override on top
# of the bill -- never continue when the run is more likely than not to cost
# somebody -- whatever the gold says.
#
# `zero_death_rate` is the exact quantity (P(nobody dies) over the rollout's
# episodes), so P(a death) is its complement. `mean_deaths` is the fallback for
# a caller whose result object predates the field: it is an expected COUNT and
# therefore only a proxy, which is why the two constants are separate rather
# than one number used two ways.
FLOOR_DEATH_CHANCE = 0.5
FLOOR_MEAN_DEATHS = 0.5

# --- and whether the floor applies to a hero nobody has paid for ---
#
# **THE FLOOR IS A CONSTANT WHERE THE GAME VARIES BY THREE ORDERS OF
# MAGNITUDE**, which is the same defect `tools/exchange_rates.py` found one
# layer down in the training reward. It fires identically for a level-1
# Highwayman (0 gold sunk, free from the coach) and a Week-22 veteran (23,750
# measured).
#
# Fran's Shambler raid on 2026-09-03 is the counterexample, from his own play:
# a level-1 party, the Highwayman died, he took `ancestors_scroll` (`limit=1`,
# one of a kind) off the fight, and his verdict was "very much worth it". The
# floor as written refuses that unconditionally -- and `advise_retreat` cannot
# even see the trinket, because trinkets are deliberately unpriced (see the
# module docstring).
#
# `scale_floor_by_investment=True` makes the floor apply only when a hero is
# worth more than the quest being risked for them. **That threshold is DERIVED,
# not invented**: both sides are already in gold, `mean_sunk` from the real
# upgrade trees and `forfeited` from the raid's own completion reward. It needs
# no new constant, and on Fran's scene it reproduces his call (0 gold against a
# real reward, so the floor stands aside) while still firing for a kitted party.
#
# **Default False**, so today's decisions are unchanged. Same discipline as
# `--stress-weight`, `--prevention-reward` and the five distribution knobs: a
# contested change ships switched off and gets measured, rather than being
# decided by whoever wrote it.
#
# **Switched ON 2026-09-23 by Fran's ruling**, after a night of raids ended at
# the entrance with nothing banked: *"those level 0 heroes are super
# expendable, as you wont probably invest on them"*, and *"retreating is worse
# the less money you have, as you prefer deaths when you are stuck"*. A party
# worth less than the quest no longer gets the floor.
SCALE_FLOOR_BY_INVESTMENT = True


@dataclass(frozen=True)
class QuestReward:
    """What completing the quest pays, from `raid.raid_instance`."""
    gold: int = 0
    heirlooms: tuple[tuple[str, int], ...] = ()
    trinkets: tuple[str, ...] = ()

    def summary(self) -> str:
        bits = [f"{self.gold:,} gold"] if self.gold else []
        bits += [f"{n}x {name}" for name, n in self.heirlooms]
        bits += [f"trinket {t}" for t in self.trinkets]
        return ", ".join(bits) or "nothing recorded"


@dataclass(frozen=True)
class RetreatAdvice:
    retreat: bool
    reason: str
    cost_of_retreating: float
    cost_of_continuing: float
    gold_per_stress: float
    # breakdown
    retreat_stress_gold: float = 0.0
    forfeited_gold: float = 0.0
    expected_stress_gold: float = 0.0
    expected_death_gold: float = 0.0
    expected_reward_gold: float = 0.0
    mean_deaths: float = 0.0
    party_at_risk: tuple[str, ...] = ()
    unpriced: tuple[str, ...] = ()
    #: Which rule actually decided — "gold" (the two bills) or "floor" (the
    #: survival override). Recorded because the log line otherwise quotes a
    #: gold figure that did not decide anything, which is how a reader ends up
    #: debugging the wrong half.
    decided_by: str = "gold"
    #: P(at least one hero dies), when the rollout could supply it.
    death_chance: Optional[float] = None

    def line(self) -> str:
        head = "RETREAT" if self.retreat else "push on"
        if self.decided_by == "floor":
            return f"{head}: {self.reason}"
        return (f"{head}: pulling out costs ~{self.cost_of_retreating:,.0f} gold, "
                f"pushing on costs ~{self.cost_of_continuing:,.0f} — {self.reason}")

    def report(self) -> str:
        out = [self.line(), ""]
        out.append(f"  retreat  = {self.retreat_stress_gold:>9,.0f} stress on the way out"
                   f"  + {self.forfeited_gold:>8,.0f} quest reward given up")
        out.append(f"  continue = {self.expected_stress_gold:>9,.0f} stress still to take"
                   f"  + {self.expected_death_gold:>8,.0f} expected deaths"
                   f"  - {self.expected_reward_gold:>8,.0f} reward earned")
        out.append(f"  at {self.gold_per_stress:.1f} gold per stress point; "
                   f"expected deaths {self.mean_deaths:.2f}"
                   + ("" if self.death_chance is None
                      else f"; P(a death) {self.death_chance:.0%}"))
        if self.decided_by == "floor":
            out.append("  DECIDED BY THE SURVIVAL FLOOR — the gold bills above "
                       "favoured pushing on and were overridden.")
        if self.party_at_risk:
            out.append("  gold sunk in the party: "
                       + ", ".join(self.party_at_risk))
        if self.unpriced:
            out.append("  also forfeited, deliberately unpriced: "
                       + "; ".join(self.unpriced))
        return "\n".join(out)


def read_quest_reward(raid_file: str | Path) -> QuestReward:
    """The completion reward from a raid save.

    `raid_instance.completion_reward.items_definition.items` is keyed by slot
    index, with the payload inside — the same shape trap as `trinkets.items`,
    where reading the keys gives `['0','1']` and nothing else.
    """
    path = Path(raid_file)
    if not path.exists():
        return QuestReward()
    root = parse_file(path)
    instance = root.get("raid_instance") if root is not None else None
    if instance is None:
        return QuestReward()
    reward = (instance.to_dict() or {}).get("completion_reward") or {}
    items = ((reward.get("items_definition") or {}).get("items") or {})
    gold = 0
    heirlooms: list[tuple[str, int]] = []
    trinkets: list[str] = []
    for entry in items.values():
        if not isinstance(entry, dict):
            continue
        kind, amount, name = entry.get("type"), int(entry.get("amount") or 0), entry.get("id") or ""
        if kind == "gold":
            gold += amount
        elif kind == "heirloom":
            heirlooms.append((str(name), amount))
        elif kind == "trinket" and name:
            trinkets.append(str(name))
    return QuestReward(gold=gold, heirlooms=tuple(heirlooms), trinkets=tuple(trinkets))


def party_investment(heroes, game_dir: str | Path = GAME_DIR,
                     profile=None, store=None) -> list[tuple[str, int]]:
    """Gold sunk per hero — what actually dies with them.

    **Pass `profile` whenever there is one.** Without it this falls back to the
    old approximation, which prices the hero by what is EQUIPPED and is wrong
    by a factor of thirty on a real campaign: measured 2026-09-02 over Fran's
    Week 22 roster, **750 gold against 23,750**. The failure is one line — a
    hero with rank-0 gear got `skill_rank = weapon_rank = 0`, so every skill
    they had bought summed to nothing and a Crusader with three 1000-gold
    unlocks valued at ZERO.

    With a profile it reads what was actually purchased out of
    `persist.upgrades.json`: unlocks beyond the class's free slots, the rank
    ladder above each unlock, real weapon and armour ranks, and locked
    positive quirks. See `data/hero_value.py`.

    The fallback is kept rather than removed because a replayed capture has no
    profile beside it, and a rough number there is better than refusing to
    price a retreat at all — but it is a floor, never the figure to quote.

    **And it is treated as an actual floor.** When a profile IS supplied, the
    per-hero result is `max(profile reading, equipment reading)`. The profile
    path is normally thirty times the larger, so coming back *under* the floor
    means the parse lost something rather than that the hero is cheap — and
    `persist.upgrades.json` is exactly the file that invites this, since
    `requirement_code` and `is_purchased` decode to nothing on all 228 entries
    of a real save. A silent under-price here is what had the live bridge
    pushing on at `P(finish intact) 0%`, so it must not be reachable by a parse
    returning less than it should.
    """
    equipped = []
    for hero in heroes:
        sunk = hero_investment(
            game_dir, hero.hero_class,
            weapon_rank=int(getattr(hero, "weapon_rank", 0) or 0),
            armour_rank=int(getattr(hero, "armour_rank", 0) or 0),
            skill_ids=list(getattr(hero, "selected_skills", ()) or ()),
        )
        equipped.append((hero.hero_class, sunk))
    if profile is None:
        return equipped

    from data.hero_value import party_value          # noqa: PLC0415
    priced = [(v.hero_class, v.total)
              for v in party_value(profile, heroes, store, str(game_dir))]
    return [(cls, max(total, floor))
            for (cls, total), (_c, floor) in zip(priced, equipped)]


def advise_retreat(heroes, rollout_result, reward: QuestReward,
                   abbey_tier: int = 0,
                   retreat_stress: float = RETREAT_STRESS,
                   current_stress: Optional[float] = None,
                   game_dir: str | Path = GAME_DIR,
                   profile=None, store=None,
                   scale_floor_by_investment: bool = SCALE_FLOOR_BY_INVESTMENT,
                   loot_ahead_gold: float = 0.0,
                   ) -> RetreatAdvice:
    """Two bills, in gold, with a survival floor over the top. Retreat when the
    smaller bill is leaving, or when the floor fires whatever the bills say.

    `rollout_result` supplies the only estimated inputs — `mean_deaths`,
    `mean_final_stress`, `completion_rate` and (when present) `zero_death_rate`
    — and is a LOWER bound on how a human would do, so this leans towards
    retreating.

    **`current_stress` is a PER-HERO MEAN, not a party total.** It is compared
    against `mean_final_stress`, which `eval_expedition.py` builds as a mean
    over the living heroes, and the difference is then scaled by party size
    exactly once. Passing a sum here silently prices the stress ahead at zero:
    `max(0.0, mean - sum)` is 0 for any party carrying real stress, which is
    what the live bridge did until 2026-09-03. Every test fixture defaulted to
    stress 0 — where a sum and a mean coincide — so nothing caught it.
    """
    rate = gold_per_stress(game_dir, abbey_tier)
    living = [h for h in heroes if getattr(h, "hp", 1) > 0]
    sunk = party_investment(living, game_dir, profile, store)
    mean_sunk = (sum(s for _c, s in sunk) / len(sunk)) if sunk else 0.0

    # **STRESS IS NOT BILLED IN GOLD, on either side.** Fran, 2026-09-23:
    # *"stress is managable, the only thing that matters is the survival
    # percent ... we should manage an average between, survives and gathers
    # most loot possible"*. What stress does to a raid -- afflictions, heart
    # attacks, a team pushed toward 100 by one retreat after another -- the
    # rollout already simulates, and it shows up here as `mean_deaths`.
    # Pricing it again in gold counted it twice: at the Abbey's 22 gold a
    # point, a free level-0 party read ~8,000 gold for staying and retreated
    # from every fight of the raid. The fields stay, at zero, so a reader of
    # the advice sees that stress was left out on purpose.
    retreat_stress_gold = 0.0
    expected_stress_gold = 0.0
    forfeited = float(reward.gold)
    # **Leaving costs NOTHING beyond what staying would have earned.** The
    # reward used to be charged here in full AND credited to staying at
    # `completion_rate * reward` -- counted twice. Live 2026-09-23: a party
    # down to its Vestal read "leaving ~3,000, staying ~3,000 -- worth pushing
    # on" and walked back into the fight that had killed two, because leaving
    # was billed the whole 3,000 reward that a lone hero was never going to
    # collect. Fran: *"now its a good opportunity to retreat, but keeps looping
    # into the combat"*. Staying's bill already carries the reward at its real
    # odds, so leaving is zero and the question is whether staying's expected
    # gain beats its expected deaths. `forfeited` stays in the advice for the
    # reader.
    cost_retreat = 0.0

    # **A death always costs something, even a free recruit's.** Fran:
    # *"we shouldn't guide the model towards losing a hero"*. A hero is worth
    # what was spent on them, and at least their share of what this quest
    # pays -- what a hero earns per raid, and so what the next raids lose
    # when one dies. Derived from the quest, not an invented constant.
    share = forfeited / len(living) if living else 0.0
    hero_value = max(mean_sunk, share)
    expected_death_gold = rollout_result.mean_deaths * hero_value
    expected_reward_gold = rollout_result.completion_rate * reward.gold
    # **Staying also collects what is still on the map**, and leaving does
    # not. Fran, 2026-09-23: the bill only counted the quest reward, so the
    # provisions already bought -- worthless once the party leaves -- and the
    # loot they were bought to reach never entered it. Scaled by the same
    # completion rate as the reward: a party that does not get there does not
    # collect it. Default 0 keeps every existing caller's answer.
    expected_reward_gold += rollout_result.completion_rate * float(loot_ahead_gold)
    cost_continue = expected_stress_gold + expected_death_gold - expected_reward_gold

    # Does the floor apply at all? With scaling on it stands aside when the
    # party is cheaper than the quest -- there is nothing there to protect that
    # the gold arithmetic is not already pricing correctly.
    floor_applies = True
    if scale_floor_by_investment:
        floor_applies = mean_sunk > forfeited

    zero_death_rate = getattr(rollout_result, "zero_death_rate", None)
    death_chance = None if zero_death_rate is None else 1.0 - float(zero_death_rate)
    if death_chance is not None:
        floored = death_chance >= FLOOR_DEATH_CHANCE
        floor_reason = (f"the run loses somebody {death_chance:.0%} of the time — "
                        f"over the {FLOOR_DEATH_CHANCE:.0%} floor, so the gold "
                        f"does not get a vote")
    else:
        floored = rollout_result.mean_deaths >= FLOOR_MEAN_DEATHS
        floor_reason = (f"{rollout_result.mean_deaths:.1f} expected deaths — over "
                        f"the {FLOOR_MEAN_DEATHS:.1f} floor, so the gold does "
                        f"not get a vote")

    floored = floored and floor_applies
    by_gold = cost_retreat < cost_continue
    retreat = by_gold or floored
    # The floor is only worth NAMING when it is what changed the answer.
    decided_by = "floor" if (floored and not by_gold) else "gold"
    if decided_by == "floor":
        reason = floor_reason
    elif retreat:
        driver = ("the expected deaths alone" if expected_death_gold > forfeited
                  else "the stress still ahead")
        reason = f"{driver} outweighs giving the quest up"
    else:
        reason = ("the quest is worth more than the damage left to take"
                  if expected_reward_gold >= expected_stress_gold + expected_death_gold
                  else "leaving costs more than finishing")

    unpriced = []
    if reward.heirlooms:
        unpriced.append(", ".join(f"{n}x {name}" for name, n in reward.heirlooms)
                        + " (heirlooms buy building upgrades, not goods)")
    if reward.trinkets:
        unpriced.append("trinket " + ", ".join(reward.trinkets)
                        + " (PLAYBOOK 1.2 values a trinket by whether a comp equips it)")

    return RetreatAdvice(
        retreat=retreat, reason=reason,
        cost_of_retreating=cost_retreat, cost_of_continuing=cost_continue,
        gold_per_stress=rate,
        retreat_stress_gold=retreat_stress_gold, forfeited_gold=forfeited,
        expected_stress_gold=expected_stress_gold,
        expected_death_gold=expected_death_gold,
        expected_reward_gold=expected_reward_gold,
        mean_deaths=rollout_result.mean_deaths,
        party_at_risk=tuple(f"{c} {s:,}" for c, s in sunk),
        unpriced=tuple(unpriced),
        decided_by=decided_by, death_chance=death_chance)


def shuffle_is_worth_undoing(rollout, party, original_order: Sequence[int],
                             zone_id: str, tier: int, num_combats: int,
                             length_band: str = "medium",
                             abbey_tier: int = 0,
                             episodes: int = 12,
                             game_dir: str | Path = GAME_DIR):
    """PLAYBOOK 2.6's first reason: retreat and re-enter to undo a bad shuffle.

    This one is not a quest forfeit — you go back in — so the bill is just the
    retreat stress against however much the ruined formation costs. Both sides
    are measured by running the SAME dungeon with the two orders, so the answer
    is in deaths and stress rather than in a formation heuristic.

    Returns `(worth_it, gold_saved, shuffled_result, restored_result)`.
    """
    rate = gold_per_stress(game_dir, abbey_tier)
    restored = [party[i] for i in original_order]
    shuffled_result = rollout.evaluate(party, zone_id, tier, num_combats,
                                       length_band=length_band, episodes=episodes)
    restored_result = rollout.evaluate(restored, zone_id, tier, num_combats,
                                       length_band=length_band, episodes=episodes)
    sunk = party_investment(party, game_dir)
    mean_sunk = (sum(s for _c, s in sunk) / len(sunk)) if sunk else 0.0

    def bill(result):
        return result.mean_deaths * mean_sunk + result.mean_final_stress * rate

    gold_saved = bill(shuffled_result) - bill(restored_result)
    cost = RETREAT_STRESS * len(party) * rate
    return gold_saved > cost, gold_saved - cost, shuffled_result, restored_result


def main() -> None:
    import argparse
    from bridge.live_state import read_live_state

    ap = argparse.ArgumentParser(description="retreat or push on, priced in gold")
    ap.add_argument("--raid", required=True, help="a persist.raid.json (a COPY)")
    ap.add_argument("--roster", default=None)
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip")
    ap.add_argument("--episodes", type=int, default=12)
    ap.add_argument("--abbey-tier", type=int, default=0,
                    help="0 un-upgraded (most expensive stress, most conservative)")
    ap.add_argument("--profile", default=None,
                    help="the save directory. WITHOUT it the party is priced "
                         "by what is equipped, which reads ~30x low on a real "
                         "campaign -- see party_investment")
    args = ap.parse_args()

    roster = args.roster or args.raid.replace("raid.json", "roster.json")
    state = read_live_state(raid_file=args.raid, roster_file=roster)
    reward = read_quest_reward(args.raid)

    from bridge.rollout import PartyMember, Rollout, band_for, infer_zone_and_tier
    from bridge.route import read_quest

    quest = read_quest(args.raid)
    rollout = Rollout(args.model)
    party = [PartyMember.from_live_hero(h) for h in state.heroes]
    combats = state.remaining_battles or 4
    zone = quest.dungeon if quest and quest.dungeon else None
    tier = quest.difficulty if quest and quest.difficulty else None
    if zone is None or tier is None:
        zone, tier = infer_zone_and_tier(state, rollout.store)
    result = rollout.evaluate(party, zone, tier, combats,
                              length_band=band_for(combats), episodes=args.episodes)

    print(f"quest reward on the table: {reward.summary()}")
    print(f"{combats} combats left in {zone} (tier {tier}), "
          f"P(finish intact) {result.success_rate:.0%}\n")
    profile = None
    if args.profile:
        from bridge.live_state import find_profile              # noqa: PLC0415
        profile = find_profile(args.profile)
    print(advise_retreat(state.heroes, result, reward,
                         abbey_tier=args.abbey_tier, profile=profile,
                         store=rollout.store).report())


if __name__ == "__main__":
    main()
