"""One expedition of the campaign: a chosen party on a chosen quest, played
by the shipped combat policy in `ExpeditionSequenceEnv`.

It reuses `bridge.rollout.Rollout` for the env and the policy (the same code
path the live bridge prices its decisions with) and reads each hero's fate
out of the env when the episode ends, because the campaign needs WHO died
and in what state each survivor came home -- `ExpeditionOutcome` only counts.

What this does NOT model yet (F1), each a known gap rather than a silence:
- the raid's own loot (rooms, curios) -- the quest reward is paid by the
  caller; `bridge/lootplan.py`'s economy is the next step;
- the provision basket: the env's own starting convention is used, not the
  plan's basket;
- the Darkest Dungeon's hand-authored maps: DD quests are sampled from the
  tier-6 mash like any other, with the plot boss scripted last. The Heart
  (`ancestor_heart_D`) has no mash group and is fielded alone -- an
  approximation of a multi-phase fight that has never been validated.
"""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from bridge.live_state import LiveHero
from bridge.rollout import QUEST_LENGTH_TO_BAND, PartyMember, Rollout, build_options
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS
from eval_expedition import run_full_expedition_episode


@dataclass
class HeroFate:
    guid: int
    died: bool
    stress: float = 0.0
    affliction_id: Optional[str] = None
    virtue_id: Optional[str] = None


@dataclass
class ExpeditionResult:
    completed: bool
    abandoned: bool
    wiped: bool
    combats_reached: int
    num_combats: int
    heroes: list[HeroFate]

    @property
    def deaths(self) -> int:
        return sum(h.died for h in self.heroes)

    @property
    def success(self) -> bool:
        """The quest is won: every fight done and somebody left to claim it."""
        return self.completed and not self.wiped


def boss_group(store, zone: str, tier: int, monsters: Sequence[str]) -> Optional[list[str]]:
    """The encounter group a plot boss is fought in: the `boss` mash entry of
    that zone and tier holding the boss monster (e.g. the Hag with her
    cauldron). None when the boss has no group -- the caller fields the
    monster alone."""
    if not monsters:
        return None
    want = monsters[0]
    for e in store.enemy_comps:
        if e.category == "boss" and e.zone_id == zone and e.tier == tier and want in e.enemy_ids:
            return list(e.enemy_ids)
    for e in store.enemy_comps:
        if want in e.enemy_ids:
            return list(e.enemy_ids)
    return [want] if want in getattr(store, "enemies", {}) else None


class ExpeditionRunner:
    """Holds one `Rollout` (env + policy), so a campaign pays the model load
    once."""

    def __init__(self, model_path: str):
        self.rollout = Rollout(model_path)
        self.store = self.rollout.store

    def run(self, party: Sequence[LiveHero], *, dungeon: str, difficulty: int,
            length: int, seed: int, boss_monsters: Sequence[str] = ()) -> ExpeditionResult:
        rng = random.Random(seed)
        band = QUEST_LENGTH_TO_BAND.get(length, "long")
        lo, hi = EXPEDITION_LENGTH_BANDS[band]
        num_combats = rng.randint(lo, hi)
        # A hero in town has no raid HP (`LiveHero.hp` is 0 there), and 0
        # would start them at Death's Door: start them full.
        members = [replace(PartyMember.from_live_hero(h), hp=None) for h in party]
        options = build_options(members)
        group = boss_group(self.store, dungeon, difficulty, boss_monsters)
        if group:
            # Empty groups are sampled as usual; only the last fight is fixed.
            options["enemy_group_script"] = [[]] * (num_combats - 1) + [group]
        env = self.rollout.env
        ep_seed = zlib.crc32(f"{dungeon}|{difficulty}|{length}|{seed}".encode()) % (2**31)
        env.unwrapped._sampler_rng.seed(ep_seed)
        out = run_full_expedition_episode(
            env, self.rollout.model, comp_name=None, hero_ids=options["hero_ids"],
            zone_id=dungeon, tier=difficulty, length_band=band, num_combats=num_combats,
            seed=ep_seed, extra_options=options)
        base = env.unwrapped
        # The final state is the ENGINE's, read exactly as the eval reads its
        # `living`: `_alive_indices` and `_carry_over_by_index` are only
        # refreshed between combats, so a death in the last fight is not in
        # them (caught by the cross-check below on its first run).
        # `engine.heroes[k]` is hero `_alive_indices[k]`, the env's own
        # positional convention.
        final = {}
        if len(base.engine.heroes) == len(base._alive_indices):
            final = {orig: base.engine.heroes[k] for k, orig in enumerate(base._alive_indices)}
        carry = base._carry_over_by_index if isinstance(base._carry_over_by_index, dict) else {}
        fates = []
        for i, h in enumerate(party):
            e = final.get(i)
            c = carry.get(i)
            if e is not None:
                died, stress = not e.is_alive(), float(e.stress)
                aff, vir = e.affliction_id, e.virtue_id
            elif c is not None and i in base._alive_indices:
                died, stress = False, float(c.stress)
                aff, vir = c.affliction_id, c.virtue_id
            else:
                died, stress, aff, vir = True, float(h.stress), None, None
            fates.append(HeroFate(guid=h.guid, died=died, stress=stress,
                                  affliction_id=aff, virtue_id=vir))
        if sum(f.died for f in fates) != out.deaths:
            # Two instruments that must agree: the env's survivor list and the
            # eval's own death count. A mismatch is a bug, never noise.
            raise RuntimeError(f"env says {sum(f.died for f in fates)} dead, "
                               f"the eval says {out.deaths}")
        return ExpeditionResult(completed=out.completed, abandoned=out.abandoned,
                                wiped=out.wiped, combats_reached=out.combats_reached,
                                num_combats=num_combats, heroes=fates)
