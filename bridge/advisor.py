"""Watch a real Darkest Dungeon fight and say what the trained policy would do.

This is layer 2 of the bridge: read the game, seed the simulator, run the
checkpoint, print the move. The human clicks it. No input injection, no
compute — and it is the first thing in this project that tests the policy
against the actual game instead of against the simulator it was trained in.

    python bridge/advisor.py --model models/dd_combat_ppo_full_expedition.zip
    python bridge/advisor.py --once --raid <snapshot> --roster <roster>

Output, one block per hero turn:

    [R1 T5] Musketeer (rank 3)
      -> AIMED SHOT  on rank 1  (Swine Chopper, 21 hp)
         alternatives: Patch Up on self 12%, Buckshot on rank 2 7%

The alternatives and their probabilities are deliberate. If a recommendation
looks wrong, the spread says whether the policy was confident or nearly
indifferent, and that is free diagnosis — the same reason
`tools/analyze_behaviour.py` reports action frequencies rather than just a
score.

**Polling, not hooking.** The game rewrites `persist.raid.json` every 1-5
seconds including mid-combat, so this watches the file's mtime and only reads
when it moves. It never opens the live save directly: `bridge.live_state`
copies first, because holding a read handle on a file the game is writing is
suspected to have contributed to a crash once (see `tools/save_watch.py`).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from bridge.live_state import GAME_DIR, SUPPLY_IDS, LiveState, find_profile, read_live_state
from bridge.retreat import QuestReward, advise_retreat, read_quest_reward
from bridge.rollout import (PartyMember, Rollout, band_for,
                            infer_zone_and_tier, quiet_torch)
from bridge.route import read_quest
from bridge.seed import SeedError, build_engine_from_live
from data.comp_loadout_loader import CompLoadoutLoader
from engine import Action
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS, ExpeditionSequenceEnv

# How many runner-up actions to show under the recommendation.
N_ALTERNATIVES = 3

# Below this simulated chance of finishing the dungeon with the party intact,
# the note says to consider turning back. A convention, and deliberately a
# coin-flip rather than a tuned number: the rollout is a LOWER bound (the policy
# plays worse than a human), so anything stricter would cry retreat constantly.
# It labels a number that is printed in full either way — the percentage is the
# output, this only names it.
# Kept only so an older caller importing it still resolves. The retreat verdict
# is no longer a probability threshold — see bridge/retreat.py and the note in
# survival_note.
RETREAT_THRESHOLD = 0.5

# The full-expedition policy's observation carries 8 expedition-context floats
# on top of the per-combat vector, so the host env has to be the sequence env
# or the widths don't match (324 vs 332). Almost all of that context is real:
# combats already finished comes from the raid's own BATTLE_COMPLETED counter,
# party size from `start_heroes_size`, stress and survivors from the roster,
# provisions from the party inventory.
#
# **How many combats this dungeon holds in total** now comes from the real
# map: `persist.map.json` keeps an unconsumed `mash_index` on every tile that
# still has a fight waiting, so `battles_completed + remaining` is the real
# length (see live_state._read_remaining_battles). The constant below is only
# the fallback for when no map file is readable, and --combats-total still
# overrides both. It shifts pacing (how freely to spend resources), never
# whether a move is legal.
_MEDIUM = EXPEDITION_LENGTH_BANDS.get("medium", (6, 10)) if isinstance(
    EXPEDITION_LENGTH_BANDS, dict) else (6, 10)
DEFAULT_TOTAL_COMBATS = (_MEDIUM[0] + _MEDIUM[1]) // 2


def _repositioning_monsters(store) -> set[str]:
    """Monster ids with at least one skill that can move somebody.

    A look-ahead recommendation is only safe while the board holds still. Rank
    is not cosmetic here: a skill's `launch` decides whether the advised hero
    can even use it, and the target rank decides what gets hit. So the one
    thing worth warning about, out of everything that changes during an enemy
    turn, is repositioning.

    Real data, not a guess — every monster skill level carries `move_self`,
    `move_target`, `push` and `pull`, and effects carry `push_distance` /
    `pull_distance`. Computed once at construction, since it is a property of
    the loaded monster data rather than of any one fight."""
    out: set[str] = set()
    for mid, mdef in store.enemies.items():
        for skill in mdef.skills:
            for level in skill.levels.values():
                if level.move_self or level.move_target or level.push or level.pull:
                    out.add(mid)
                    break
                for eff_name in (level.effects or []):
                    eff = store.effects.get(eff_name)
                    if eff and (eff.push_distance or eff.pull_distance):
                        out.add(mid)
                        break
                if mid in out:
                    break
            if mid in out:
                break
    return out


class Advisor:
    """Holds the loaded game data, the observation machinery and the policy.

    Construction is the slow part (`DarkestDungeonEnv` parses the whole game
    install, ~1-2 s); after that a recommendation is milliseconds, so the poll
    loop never pays for it again. The env is reused rather than reimplemented
    precisely so the policy sees the identical observation vector and action
    mask it was trained with."""

    def __init__(self, model_path: Optional[str] = None,
                 total_combats: int = DEFAULT_TOTAL_COMBATS,
                 combats_total_explicit: bool = False,
                 survival_episodes: int = 0):
        self.env = ExpeditionSequenceEnv()
        self.store = self.env.store
        self.total_combats = max(1, total_combats)
        # An explicit --combats-total is the operator overriding the map, so it
        # must not be quietly replaced by what the map says.
        self._combats_total_explicit = combats_total_explicit
        self.model = None
        #: Why the model is not loaded, when a path was given and refused.
        #: Empty when there is a model or when none was asked for.
        self.model_refused = ""
        if model_path:
            from sb3_contrib import MaskablePPO
            quiet_torch()
            model = MaskablePPO.load(model_path, device="cpu")
            # A CHECKPOINT ONLY RUNS AT ITS OWN OBSERVATION WIDTH, and SB3
            # does not find out until `predict` -- which is the first combat
            # turn, in the middle of a raid, once per turn, forever. The width
            # is a property of the LOADED DATA as well as of the code (the
            # enemy-type one-hot), so this is reachable by a pure loader fix
            # with no change in `env/` at all, and it happened on 2026-09-02.
            #
            # Refusing here costs the combat advice and keeps everything else:
            # route, torch, curio and camp decide without a policy, and the
            # journal still records every move the human plays. That is the
            # difference between a usable session and an exception per turn.
            want = self.env.observation_space.shape
            got = getattr(model.observation_space, "shape", None)
            if got is not None and tuple(got) != tuple(want):
                self.model_refused = (
                    f"{Path(model_path).name} is {got[0]}-wide and this engine "
                    f"produces {want[0]} — no checkpoint exists at the current "
                    f"width, so combat advice AND the survival forecast are off "
                    f"until a retrain. Route, torch, curio and camp still "
                    f"decide, and every move you play is still recorded.")
                print(f"note: {self.model_refused}")
            else:
                self.model = model
        # {"<hero_id>_<skill_id>": "Display Name"} — several skills were
        # renamed after their internal id was set (Occultist's `bloodlet`
        # displays as "Sacrificial Stab"), so the id is not a usable label.
        self._skill_names = CompLoadoutLoader._load_english_entries(
            GAME_DIR, "combat_skill_name_")
        # {rank: display name} for the current fight. store.enemies[].name is
        # the internal id; the save carries the name actually on screen
        # ("Swine Chopper"), which is what the player is looking at.
        self._enemy_names: dict[int, str] = {}
        # {hero roster guid: {skill_id: uses}} for the battle in progress.
        # Per-battle limits are not in the save (see seed._sync_skill_uses), so
        # this is the bridge remembering what it played. Keyed on
        # `battles_completed`, which is constant within a fight and comes from
        # the raid's own stat database.
        self._skill_uses: dict[int, dict[str, int]] = {}
        self._uses_battle: object = None
        self._repositioners = _repositioning_monsters(self.store)
        # The retreat valuation. Its own env, never this one: `_install` writes
        # a seeded engine into `self.env` in place, and a rollout through the
        # same instance would overwrite the board about to be advised on.
        self._survival_episodes = max(0, survival_episodes)
        # THE SAME CHECKPOINT, LOADED A SECOND TIME, and it is the one that
        # actually crashed: `Rollout` passes `env=`, so SB3's
        # `check_for_correct_spaces` raises at LOAD rather than at `predict`.
        # Guarding only the advice half above left `--survival` — which
        # `bat/combat_advise.bat` passes by default — dying on startup with a
        # traceback instead of the clear note the width check exists to print.
        #
        # A refused checkpoint costs the retreat valuation too: without a
        # policy there is nothing to simulate the rest of the dungeon with. The
        # dungeon layers and the recording are unaffected.
        self._rollout = None
        if self._survival_episodes and model_path and not self.model_refused:
            try:
                self._rollout = Rollout(model_path)
            except Exception as exc:                       # noqa: BLE001
                # Belt as well as braces. The width check above is the KNOWN
                # failure; this catches the rest (a truncated checkpoint, a
                # torch version bump) so a recording session degrades to the
                # layers that need no policy instead of dying at startup with
                # a traceback. Losing the retreat valuation is not worth losing
                # the session.
                self.model_refused = (self.model_refused
                                      or f"the retreat valuation could not load: {exc}")
                print(f"note: {self.model_refused}")
        # The active quest and its reward, read once from the raid save when a
        # path is known. `raid_instance` carries both, so zone/tier no longer
        # have to be guessed from the enemy line and the retreat bill has a real
        # number on the other side of it.
        self._quest = None
        self._reward = None
        # The save directory the quest was read from, when it is a real profile
        # rather than a replayed capture. It is what prices a DEATH: without it
        # `party_investment` falls back to the equipped-gear approximation,
        # which is wrong by a factor of thirty on a real campaign (750 gold
        # against 23,750 over Fran's Week 22 roster) and is what had the bridge
        # advising "worth pushing on" at P(finish intact) 0%.
        self._profile = None
        self._survival_key = None
        self._survival_text = ""
        # What the last `recommend()` actually chose, kept structured beside the
        # printed block. `bridge/run.py` needs the Action and the board it was
        # chosen on to mark the recommendation against what the player then did;
        # re-deriving them would mean seeding the engine a second time from a
        # save that has since moved. Nothing here reads them back — they are
        # write-only state for a caller that wants more than the text.
        self.last_action: Optional[Action] = None
        self.last_engine = None
        self.last_provisional = False
        self.last_confidence = 0.0
        # The WHOLE ranked shortlist, best first, as `(action, probability)`.
        # `last_action` is its head and stays the recommendation.
        #
        # It exists because **an action the policy ranks first can have no
        # control on screen**: retreat is its own button and the tutorial does
        # not draw one, a provision is used from the inventory, and the
        # Abomination can be offered a beast-form skill that is not on his
        # bar. `bridge/act.py` used to refuse and return WITHOUT marking the
        # turn played, so the same refusal republished every poll forever and
        # the run stalled in place. An actuator that can see the next-best
        # legal move can play it instead of deadlocking.
        #
        # Kept in the same order the block prints, so "alt:" in the log and the
        # fallback the actuator takes are the same list.
        self.last_actions: list[tuple[Action, float]] = []

    # ── naming ──

    def skill_label(self, hero_class: str, skill_id: str) -> str:
        if skill_id.startswith("__"):
            return skill_id.strip("_").replace("_", " ").upper()
        return self._skill_names.get(f"{hero_class}_{skill_id}",
                                     skill_id.replace("_", " ")).upper()

    def _target_label(self, engine, actor, action: Action) -> str:
        if action.target_id is None or action.target_id < 0:
            return "on self"
        # Which side a rank refers to comes from the skill, not the Action —
        # Action carries only (hero_id, skill_id, target_id).
        sd = None
        if actor is not None and not action.skill_id.startswith("__"):
            sd = engine.get_skill_data(action.skill_id, level=actor.skill_tier)
        rank = action.target_id
        allies = bool(sd and sd.targets_allies)
        pool = engine.heroes if allies else engine.enemies
        # By occupied ranks, not by front rank: a size-2 monster answers to
        # both of the ranks it stands on.
        hit = next((c for c in pool
                    if rank in c.occupied_positions and not c.is_dead), None)
        aoe = " (AoE)" if sd is not None and sd.is_aoe else ""
        if hit is None:
            return f"on rank {rank + 1}{aoe}"
        name = hit.name if hit.is_hero else self._enemy_names.get(hit.id, hit.name)
        who = name if hit.is_hero else f"{name}, {hit.hp} hp"
        return f"on rank {rank + 1} ({who}){aoe}"

    def describe(self, engine, action: Action) -> str:
        actor = next((h for h in engine.heroes if h.id == action.hero_id), None)
        cls = actor.definition_id if actor else ""
        if action.skill_id.startswith("__move"):
            return f"MOVE to rank {action.target_id + 1}"
        return f"{self.skill_label(cls, action.skill_id)} {self._target_label(engine, actor, action)}"

    # ── the decision ──

    def _install(self, engine, state: LiveState) -> None:
        """Point the host env at the seeded engine and fill in the expedition
        context its observation adds.

        These are private attributes of `ExpeditionSequenceEnv` and that is
        deliberate: reusing the env's own `_get_obs`/`action_masks` is the only
        way to guarantee the policy sees the exact vector it was trained on. A
        reimplementation here would drift silently the first time the layout
        changes."""
        env = self.env
        env.engine = engine
        # Keyed by the seeded combatant's OWN id, never by a board rank. The
        # save's enemy list is a list of monsters, and its index only equals a
        # rank while every monster is size 1 — put a size-2 Bloodletter on
        # ranks 1-2 and every name after it shifts one rank forward, which is
        # exactly how a rank-4 Brigand Fusilier got announced as rank 3.
        self._enemy_names = {sim.id: (live.name or live.enemy_id)
                             for live, sim in zip(state.enemies, engine.enemies)}
        env._original_hero_ids = [h.hero_class for h in state.heroes] or ["?"]
        # start_heroes_size counts who embarked; the observation's alive_frac
        # is measured against that, not against who is still standing.
        if state.start_party_size > len(env._original_hero_ids):
            env._original_hero_ids += ["?"] * (
                state.start_party_size - len(env._original_hero_ids))
        total = self.total_combats
        if not self._combats_total_explicit and state.remaining_battles is not None:
            # The dungeon's real length: fights already won plus the encounter
            # tiles it has not spent yet.
            total = max(1, state.battles_completed + state.remaining_battles)
        env._num_combats = total
        env._combat_idx = min(state.battles_completed, total)
        env._provision_inventory = {
            pid: int(state.provisions.get(pid, 0)) for pid in SUPPLY_IDS}
        env._rebuild_action_table()

    def pending_repositioners(self, state: LiveState, count: int) -> list[str]:
        """Display names of the enemies acting before the advised hero that
        are able to move somebody, so the preview can say when to distrust it."""
        # Dead-but-not-yet-a-corpse records stay in `battle.enemies` (HP goes
        # negative first), and they stay in the initiative queue too, so
        # without this the preview warned that a monster at -8 HP might
        # reposition somebody.
        by_guid = {e.battle_guid: e for e in state.enemies
                   if not e.is_corpse and e.hp > 0}
        names: list[str] = []
        for entry in state.pending_queue()[:count]:
            live = by_guid.get(entry.guid)
            if live and live.enemy_id in self._repositioners:
                label = live.name or live.enemy_id
                if label not in names:
                    names.append(label)
        return names

    def note_skill_used(self, hero_guid: int, skill_id: str) -> None:
        """Record a skill the bridge actually played, for the limit check.

        Only what was CONFIRMED by the save is worth recording: a use that
        never landed would suppress a legal skill for the rest of the fight,
        which is a worse failure than the one this fixes.
        """
        if not skill_id or skill_id.startswith("__"):
            return
        used = self._skill_uses.setdefault(hero_guid, {})
        used[skill_id] = used.get(skill_id, 0) + 1

    def forget_skill_uses(self) -> None:
        """A fight ended. Nothing is spent in the next one."""
        self._skill_uses = {}
        self._uses_battle = None

    def recommend(self, state: LiveState, skip: int = 0) -> str:
        """One printable block for the current turn, or a one-line reason why
        there is nothing to recommend.

        `skip` walks past heroes whose turn would not land — see
        `LiveState.next_hero`. It is a recovery for the ~2% of positions where
        the save's turn order and the screen disagree, never a preference.
        """
        self.last_action, self.last_engine, self.last_confidence = None, None, 0.0
        self.last_actions = []
        hero_entry, enemies_first = state.hero_for_turn(skip)
        if hero_entry is None:
            return f"[R{state.battle_round}] waiting - no hero left to act this round"

        if state.battles_completed != self._uses_battle:
            self._uses_battle = state.battles_completed
            self._skill_uses = {}
        engine = build_engine_from_live(state, self.store, actor=hero_entry,
                                        skill_uses=self._skill_uses)
        self.last_engine = engine
        self.last_provisional = bool(enemies_first)
        self._install(engine, state)

        actor_id = engine.get_current_actor_id()
        if actor_id is None:
            return f"[R{state.battle_round}] waiting - could not place the turn"

        actor = engine.heroes[actor_id]
        # Reading past enemy turns is what keeps advice on screen through the
        # save's silent stretches, but the board WILL move while they act, so
        # say so rather than presenting it as the current position.
        lead = f"[R{state.battle_round}]"
        warn = ""
        if enemies_first:
            plural = "s" if enemies_first > 1 else ""
            lead += f" after {enemies_first} enemy turn{plural} ->"
            movers = self.pending_repositioners(state, enemies_first)
            warn = ("\n  (provisional; refreshes when they finish"
                    + (f" - {', '.join(movers)} can shuffle ranks)" if movers else ")"))
        header = (f"{lead} {actor.name} (rank {actor.position + 1}, "
                  f"{actor.hp}/{actor.max_hp} hp, {actor.stress} stress){warn}")
        status = _board_status(engine)
        if status:
            header += f"\n  {status}"

        mask = self.env.action_masks()
        if not mask.any():
            return f"{header}\n  -> no legal action (the engine sees none — likely a seeding gap)"
        if self.model is None:
            legal = [self.describe(engine, a) for a in self.env._action_map.values()]
            return f"{header}\n  legal: " + "; ".join(legal)

        obs = self.env._get_obs()
        probs = self._action_probs(obs, mask)
        order = np.argsort(-probs) if probs is not None else None
        if order is None:
            idx, _ = self.model.predict(obs, action_masks=mask, deterministic=True)
            order = [int(idx)]

        lines = [header]
        shown = 0
        for i, idx in enumerate(order):
            action = self.env._action_map.get(int(idx))
            if action is None:
                continue
            pct = f"{probs[idx] * 100:.0f}%" if probs is not None else ""
            conf = float(probs[idx]) if probs is not None else 0.0
            self.last_actions.append((action, conf))
            if shown == 0:
                self.last_action = action
                self.last_confidence = conf
                lines.append(f"  -> {self.describe(engine, action)}   {pct}")
            else:
                lines.append(f"     alt: {self.describe(engine, action)}  {pct}")
            shown += 1
            if shown > N_ALTERNATIVES:
                break
        survival = self.survival_note(state, engine)
        if survival:
            lines.append(f"  {survival}")
        return "\n".join(lines)

    def load_quest(self, raid_file) -> None:
        """Read the active quest and its reward once, from `raid_instance`.

        Cheap (one DSON parse of a file already being polled) and it removes
        two guesses at a stroke: zone and tier stop being inferred from the
        enemy line, and the retreat bill gets a real number on the other side
        of it — the quest's own completion reward.
        """
        try:
            self._quest = read_quest(raid_file)
            self._reward = read_quest_reward(raid_file)
        except Exception:
            self._quest = self._reward = None
        # The profile is the raid save's own directory — but only when it holds
        # the upgrade ledger a hero's real value is read from. A replayed
        # capture in `archive/traces/` sits beside no such file, and pointing
        # `party_value` at one would price the party by whatever it happened to
        # find. Absent means the documented fallback, not a wrong number.
        try:
            folder = Path(raid_file).parent
            self._profile = folder if (folder / "persist.upgrades.json").exists() else None
        except Exception:
            self._profile = None

    # ── should we still be here? ──

    def survival_note(self, state: LiveState, engine=None) -> str:
        """How likely the party is to finish what is left of this dungeon.

        The highest-variance decision in Darkest Dungeon is not which skill to
        use, it is whether to keep going — and until now nothing in this project
        addressed it, because a policy answers "what do I do this turn" and this
        needs a *valuation*. `bridge/rollout.py` supplies one by simulating the
        remaining combats from the party's real current state.

        Off unless `--survival` is passed: it costs a second env and a second
        checkpoint load at startup, and a fresh measurement is far slower than a
        poll interval. Cached per (combat, party HP/stress), so it recomputes
        when the position materially changes and not once a second.

        **The verdict is priced in gold, not in probability.** It used to read
        "P(finish intact) < 50% -> consider retreating", which was never
        mis-calibrated so much as mis-framed: PLAYBOOK 2.6 compares the cost of
        repairing four heroes against repairing one plus losing the dungeon.
        `bridge/retreat.py` does that subtraction with the Abbey's real stress
        price, the hero upgrade trees, and the quest's own completion reward, so
        the rollout now supplies inputs to a bill rather than a threshold.

        Zone and difficulty come from `raid.raid_instance` when it is available
        — it carries the whole active quest — and fall back to inference from
        the enemy line otherwise. No source, no note: a made-up bracket would
        answer a different question than the one asked.

        `engine` is optional because the same question is worth asking BETWEEN
        fights, where there is no board to infer a zone from. Without one, only
        the `raid_instance` path can answer, which is the honest outcome — the
        enemy-line fallback needs enemies."""
        if self._rollout is None or not state.heroes:
            return ""
        remaining = state.remaining_battles
        if remaining is None or remaining <= 0:
            return ""
        key = (state.battles_completed, remaining,
               tuple((round(h.hp), round(h.stress)) for h in state.heroes))
        if key == self._survival_key:
            return self._survival_text
        zone_id = tier = None
        if self._quest is not None and self._quest.dungeon and self._quest.difficulty:
            zone_id, tier = self._quest.dungeon, self._quest.difficulty
        elif engine is not None:
            inferred = infer_zone_and_tier(
                self.store, [e.definition_id for e in engine.enemies])
            if inferred is None:
                return ""
            zone_id, tier = inferred
        else:
            return ""
        party = [PartyMember.from_live_hero(h) for h in state.heroes]
        result = self._rollout.evaluate(
            party, zone_id=zone_id, tier=tier, num_combats=remaining,
            length_band=band_for(remaining), episodes=self._survival_episodes)
        advice = advise_retreat(state.heroes, result, self._reward or QuestReward(),
                                profile=self._profile, store=self.store)
        verdict = ("RETREAT" if advice.retreat else "worth pushing on")
        self._survival_key = key
        self._survival_text = (
            f"{remaining} combat{'s' if remaining != 1 else ''} left in {zone_id} "
            f"(tier {tier}): P(finish intact) {result.success_rate * 100:.0f}%"
            f" ±{result.standard_error * 100:.0f}, expected deaths "
            f"{result.mean_deaths:.1f}\n"
            f"  leaving costs ~{advice.cost_of_retreating:,.0f} gold, "
            f"staying ~{advice.cost_of_continuing:,.0f} — {verdict}"
            # When the survival floor is what decided, the two gold figures did
            # NOT make the call and printing them alone is how a reader ends up
            # debugging the wrong half — that is the whole reason the old log
            # read "staying ~-375 — worth pushing on" beside 3.8 expected deaths.
            + (f"\n  ({advice.reason})" if advice.decided_by == "floor" else ""))
        return self._survival_text

    def _action_probs(self, obs: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
        """Full masked action distribution, for the alternatives line. Returns
        None if the SB3 internals move — the recommendation itself does not
        depend on this, so it degrades to just the argmax rather than
        failing."""
        try:
            import torch
            obs_t, _ = self.model.policy.obs_to_tensor(obs)
            with torch.no_grad():
                dist = self.model.policy.get_distribution(obs_t, action_masks=mask)
                return dist.distribution.probs[0].cpu().numpy()
        except Exception:
            return None


def _board_status(engine) -> str:
    """The temporary state the seeded board is carrying — DoTs, guard, riposte.

    Printed because it is the half of the position a screenshot of the health
    bars does not show, and because it is the newest part of the bridge: if the
    line disagrees with what the game is displaying, that is a seeding bug and
    this is where it becomes visible. `_sync_buffs` counts anything it could not
    map, and that count is shown too rather than being silently swallowed."""
    notes = []
    for c in engine.heroes + engine.enemies:
        if not c.is_alive():
            continue
        for kind, dots in (("bleed", c.bleeds), ("blight", c.blights)):
            if dots:
                notes.append(f"{c.name} {kind} {sum(d.damage_per_tick for d in dots)}"
                             f"/turn x{max(d.remaining_ticks for d in dots)}")
        if c.guard_target_id is not None:
            guarded = engine._find_combatant(c.guard_target_id)
            notes.append(f"{c.name} guards {guarded.name if guarded else '?'}")
        if c.active_riposte:
            notes.append(f"{c.name} riposte x{c.active_riposte.remaining_rounds}")
    notes.append(_stall_note(engine))
    unmapped = getattr(engine, "seed_unmapped_buffs", 0)
    if unmapped:
        notes.append(f"{unmapped} buff(s) not mapped")
    return " | ".join(n for n in notes if n)


def _stall_note(engine) -> str:
    """Where this fight stands against the stall clock, and how close it is to
    being over.

    Added because the state was invisible from the outside: the two stall
    features sit in the observation the policy reads, but nothing was printed,
    so "I cannot tell when it thinks it is stalling" was a fair complaint and
    an unfalsifiable one. A reinforcement wave arriving out of nowhere is the
    visible consequence of a counter nobody can see.

    Reads the same fields `_check_stall` writes, and reports the exemptions
    too: a fight holding a `disable_stall_penalty` monster (brigand_blood at
    every tier, most bosses) cannot stall at all, so a low counter there means
    something different from a low counter elsewhere."""
    rules = engine.rules
    living = [e for e in engine.enemies if e.is_alive()]
    defs = [d for d in (engine.store.enemies.get(e.definition_id) for e in living) if d]
    left = f"{len(living)} enemy left" if len(living) == 1 else f"{len(living)} enemies left"
    if any(d.disable_stall_penalty for d in defs):
        return f"{left}, stall disabled here"
    # SIZE, not heads -- one size-2 monster is already over the threshold, so
    # a head count told the player they were stalling when they were not.
    if engine.effective_enemy_stall_size() > rules.stall_enemy_size_threshold:
        return left
    # One threshold for both paths now: an accelerated stall reaches it faster
    # because the COUNTER climbs by 5 a round, not because the bar moves.
    threshold = rules.stall_round_summon_effect_threshold
    if defs and all(d.accelerate_stall_penalty for d in defs):
        left += ", accelerated"
    # rules.json numbers load as floats; printed as ints because they are
    # round counts and "stall 0/4.0" reads like a bug.
    return (f"{left}, stall {engine.stall_counter}/{int(threshold)}"
            f" ({engine._stall_invalidating_actions_this_round}/"
            f"{int(rules.stall_number_of_invalidating_skills_threshold)} real actions this round)")


def _watch(advisor: Advisor, profile: Path, interval: float, duration: float,
           log_path: Optional[Path] = None) -> None:
    """Poll the live save and print one block per hero turn.

    `log_path` records what was ADVISED, which is the one thing a save capture
    cannot reconstruct afterwards. Every acceptance run so far has been read
    back against the captured saves, but the recommendation itself only ever
    existed in the terminal and in whoever was watching it — so "the advisor
    said rank 3" was testimony, not evidence, and pairing the two took a live
    report to even notice. Written line-buffered and flushed per block, since
    the interesting runs are the ones that end in a crash or a Ctrl-C."""
    raid = profile / "persist.raid.json"
    roster = profile / "persist.roster.json"
    log = open(log_path, "a", encoding="utf-8", buffering=1) if log_path else None

    def emit(text: str) -> None:
        print(text)
        if log is not None:
            log.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
            log.flush()

    advisor.load_quest(raid)
    print(f"watching {profile}\n(play; one block per hero turn. Ctrl-C to stop)\n")
    if log is not None:
        print(f"logging recommendations to {log_path}\n")
    last_sig = None
    last_key = None
    start = time.time()
    try:
        while time.time() - start < duration:
            time.sleep(interval)
            try:
                sig = (raid.stat().st_mtime, raid.stat().st_size) if raid.exists() else None
            except OSError:
                continue
            if sig == last_sig:
                continue
            last_sig = sig
            if sig is None:
                if last_key is not None:
                    emit("-- raid over --")
                    last_key = None
                continue
            # Pass the PROFILE, not the two paths: read_live_state only stages
            # (copies before parsing) when it resolves the files itself, and
            # parsing the live save in place is the thing both this module and
            # save_watch.py exist to avoid.
            state = read_live_state(profile=profile)
            if not state.in_battle:
                continue
            # Key on WHO is up next, not on the save's `currentturn`. That
            # field only moves after its turn has resolved, so keying on it
            # printed every recommendation one turn late — when the game asked
            # for the Man-at-Arms, the screen still showed the Vestal's move.
            # The next actor is derived from the turns-taken counters instead
            # (see LiveState.current_actor), which lands a full save-write
            # earlier, i.e. before the turn is played.
            # Key on the next HERO, not on the current actor: enemy turns in
            # between must not re-trigger, and must not blank the advice
            # either. Their count is part of the key so the header updates as
            # they are consumed.
            hero, enemies_first = state.next_hero()
            if hero is None:
                continue
            key = (state.battle_round, hero.guid, hero.turns_taken, enemies_first)
            if key == last_key:
                continue
            last_key = key
            try:
                emit(advisor.recommend(state) + "\n")
            except SeedError as exc:
                emit(f"[R{state.battle_round} T{state.battle_turn}] cannot seed: {exc}\n")
    except KeyboardInterrupt:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/dd_combat_ppo_full_expedition.zip",
                    help="checkpoint to consult; omit the file to just list legal actions")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--raid", default=None, help="read one captured snapshot instead of the live game")
    ap.add_argument("--roster", default=None)
    ap.add_argument("--once", action="store_true", help="print one recommendation and exit")
    ap.add_argument("--combats-total", type=int, default=None,
                    help="override how many combats this dungeon holds; by default "
                         "it is read from persist.map.json (affects resource "
                         "pacing only)")
    ap.add_argument("--log", default=None, metavar="FILE",
                    help="append every recommendation here — the save captures "
                         "record the board but never what was advised")
    ap.add_argument("--survival", type=int, nargs="?", const=12, default=0,
                    metavar="EPISODES",
                    help="also estimate the chance of finishing the dungeon "
                         "intact, by simulating the remaining combats this many "
                         "times (default 12). Costs a second env and checkpoint "
                         "load at startup")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--duration", type=float, default=3600.0)
    args = ap.parse_args()

    model = args.model if args.model and Path(args.model).exists() else None
    if args.model and model is None:
        print(f"note: {args.model} not found — listing legal actions instead of recommending")
    advisor = Advisor(model,
                      total_combats=args.combats_total or DEFAULT_TOTAL_COMBATS,
                      combats_total_explicit=args.combats_total is not None,
                      survival_episodes=args.survival)

    if args.raid or args.once:
        if args.raid:
            state = read_live_state(raid_file=args.raid,
                                    roster_file=args.roster or find_profile(
                                        args.profile) / "persist.roster.json")
            advisor.load_quest(args.raid)
        else:
            state = read_live_state(profile=args.profile)
            advisor.load_quest(find_profile(args.profile) / "persist.raid.json")
        if not state.in_battle:
            print("not in a battle")
            return 0
        print(advisor.recommend(state))
        return 0

    _watch(advisor, find_profile(args.profile), args.interval, args.duration,
           Path(args.log) if args.log else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
