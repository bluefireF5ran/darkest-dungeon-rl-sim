from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import random

import numpy as np
import gymnasium as gym
from gymnasium import spaces

from engine import SeededRNG, CombatEngine, Action
from engine.combatant import (CombatantState, BURN_BASE_DECAY,
                              burn_total_outstanding)
from data.data_loader import DataLoader
from data.store_cache import load_store_cached


# Re-exported, not defined: `data/paths.py` owns these, and about 33 files
# import them from here. Keep both names exported.
from data.paths import CSV_DIR, GAME_DIR  # noqa: F401

# Re-exported, not defined here: env/reward_config.py owns these.
from .reward_config import (  # noqa: F401
    ENEMY_DEAD_SCORE, HERO_DEAD_SCORE, RECOVERY_HP_FRACTION,
    RECOVERY_STRESS_FLOOR, REWARD_LOSE, REWARD_RETREAT, REWARD_WIN,
    SHAPING_GAMMA, STALL_PHASE_MAX_ENEMIES, STRESS_TICK_PENALTY,
    TURN_PENALTY_STALL, TURN_PENALTY_THREAT, W_AFFLICTED,
    W_BLOCKING_CORPSE, W_DEATHS_DOOR, W_ENEMY_DOT, W_ENEMY_STUNNED,
    W_HERO_GUARDED, W_HERO_RIPOSTE, W_INCOMING_DAMAGE, W_INCOMING_STRESS,
    W_STRESS, W_VIRTUOUS)

# Re-exported, not defined here: env/action_space.py owns these.
from .action_space import (  # noqa: F401
    ACTION_ABANDON, ACTION_MOVE_BASE, ACTION_PASS, ACTION_PROVISION_BASE,
    ACTION_RETREAT, ACTION_SPACE_SIZE, MAX_SKILL_SLOTS,
    PROVISION_ACTION_ORDER, RANKS, mask_fn)

# Re-exported, not defined here: env/obs_layout.py owns these.
from .obs_layout import (  # noqa: F401
    ENEMY_SLOT_SCALARS, GLOBAL_FEATURES, HERO_HEAL_HEADROOM,
    HERO_HEAL_HEADROOM_INDEX, HERO_SLOT_SCALARS, HERO_STATE_SCALARS,
    HERO_THREAT_FORECAST, MORTALITY_TIER_RANK, RETREAT_CHANCE_FROM_END,
    SKILL_MECHANIC_FEATURES, SKILL_SLOT_FEATURES, STAT_BUCKETS,
    _RECEIVED_IS_BAD, _STAT_BUCKET_BY_SUBTYPE, _STAT_BUCKET_BY_TYPE,
    _buff_min_duration, _burn_total, _clamp01, _dot_total, _hero_has_modes,
    _hero_mode_index, _is_doomed, _outstanding_dot, _rank_bits,
    _stat_bucket, _uses_remaining)


class DarkestDungeonEnv(gym.Env):
    metadata = {"render_modes": ["human", "ansi"]}

    def __init__(self, render_mode: Optional[str] = None, store=None,
                 stress_weight: float = 1.0,
                 slot_permute: bool = False, blind_forecast: bool = False,
                 prevention_reward: bool = False):
        super().__init__()
        self.render_mode = render_mode
        # THE CONTROL ARM for the threat forecast. When set, the nineteen
        # forecast features are emitted as zero -- at their SOURCE, so the
        # observation width and every other index are untouched and the two
        # arms differ in exactly the numbers under test.
        #
        # `tools/ablate_forecast.py` shows the trained policy READS them
        # (pooled McNemar p=0.0132) but cannot show they made it BETTER:
        # zeroing at eval time is off-distribution, and a policy can be hurt
        # by nonsense input it would never have been helped by. Only a second
        # training run answers that.
        self._blind_forecast = bool(blind_forecast)
        # `--prevention-reward`. Default OFF and proven byte-identical, so the
        # shipped checkpoint's distribution stays reproducible and the retrain
        # gets a clean control arm -- the same discipline the five
        # randomisation knobs follow.
        self._prevention_reward = bool(prevention_reward)
        # `store` lets a caller hand in an already-built DataStore instead of
        # paying ~1.0 s to re-parse the game install. The store is read-only
        # shared data, so sharing one across envs is safe -- and the default
        # is unchanged, so every existing construction behaves identically.
        #
        # This exists because the suite built the store ~98 times per run
        # (~98 s of a 275 s baseline). It also makes N-worker training cheap:
        # each SubprocVecEnv worker loads a 2.3 MB pickle instead of walking
        # the Steam install. See data/store_cache.py.
        self.store = store if store is not None else load_store_cached(CSV_DIR, GAME_DIR)
        self.rng: Optional[SeededRNG] = None
        self.engine: Optional[CombatEngine] = None
        self._action_table: dict[int, list[Action]] = {}
        self._valid_actions: list[Action] = []
        # Fixed-index -> concrete Action for the CURRENT actor, rebuilt every
        # turn by _rebuild_action_table. Keys are stable action-space indices
        # (see the layout comment above), not positions in a volatile list.
        self._action_map: dict[int, Action] = {}
        # Discounted recovery turns spent in the CURRENT combat — reset by
        # reset() here and by ExpeditionSequenceEnv._start_next_combat for
        # each internal combat of an expedition.
        self._recovery_turns_used = 0

        # Stable one-hot vocabularies, built once from the loaded data so the
        # observation layout is deterministic across runs and models.
        self._hero_class_index = {hid: i for i, hid in enumerate(sorted(self.store.heroes))}
        # (skill_id, level) -> the payload vector. Static per skill level and
        # rebuilt 7x per env step without it; see _skill_mechanics.
        self._skill_mechanics_memo: dict = {}
        # definition_id -> the max-pooled payload of that monster's whole
        # moveset. Static per monster, and read for 4 enemy slots every step.
        self._enemy_caps_memo: dict = {}
        # HOW MUCH STRESS IS WORTH, as a multiplier on both stress terms.
        #
        # 1.0 is byte-identical to every archived checkpoint. The reason to
        # move it is a measured misalignment between what the policy is trained
        # on and what it is graded on: `eval_expedition._run_quality` -- the
        # thing `run_score` is built from, and the thing Fran's objective names
        # -- gives stress **half** the weight of a hero's score, while the
        # training return gives it about **9%**. Over a 15-combat run the
        # combat wins and the completion bonus are worth ~85 and the two stress
        # terms at most ~8.
        #
        # Scaling both together is deliberate: `STRESS_TICK_PENALTY` charges
        # stress when it lands and `TERMINAL_STRESS_PENALTY_WEIGHT` charges
        # what is left at the end, and moving only one changes the shape of the
        # incentive rather than its size.
        self.stress_weight = float(stress_weight)

        # Slot-permutation probe (off unless `slot_permute` is passed). See
        # `_slot_order` for why this must reach BOTH consumers.
        self._slot_perm_rng = random.Random(0) if slot_permute else None
        self._slot_perms: dict = {}
        self._hero_caps_memo: dict = {}
        enemy_types = sorted({t for e in self.store.enemies.values() for t in e.enemy_types})
        self._enemy_type_index = {t: i for i, t in enumerate(enemy_types)}

        self.action_space = spaces.Discrete(ACTION_SPACE_SIZE)
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.base_obs_size,), dtype=np.float32
        )

        # Instance attrs (not just the module constants) so a subclass
        # (ExpeditionSequenceEnv) can rebalance win/lose magnitude for a
        # multi-combat episode without affecting this class or ExpeditionEnv.
        self._reward_win = REWARD_WIN
        self._reward_lose = REWARD_LOSE
        self._reward_retreat = REWARD_RETREAT

        # How much of self.engine.log render() has already shown — lets
        # render() print only the NEW action lines (skill used, target,
        # hit/miss/crit) since the last call, instead of just a static HP/
        # position/stress snapshot. Robust to self.engine.log being reset
        # to [] out from under a live engine (ExpeditionSequenceEnv starting
        # its next combat) via the length check in render() itself, so
        # nothing needs to reset this cursor explicitly.
        self._render_log_cursor = 0

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[dict] = None,
    ) -> tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        self._reset_slot_permutation()

        hero_ids = (options or {}).get("hero_ids", ["crusader", "crusader"])
        enemy_ids = (options or {}).get("enemy_ids", ["cultist_brawler_A", "cultist_brawler_A"])
        torch_level = (options or {}).get("torch_level", 100.0)
        zone_id = (options or {}).get("zone_id", "") or ""
        hero_levels = (options or {}).get("hero_levels")
        hero_skill_ids = (options or {}).get("hero_skill_ids")
        provisions = (options or {}).get("provisions")
        # Starting HP/stress/Death's-Door/affliction, same mechanism
        # ExpeditionSequenceEnv uses between combats. Exposed here so a caller
        # can open on a party that is ALREADY damaged — which is what asking
        # "does this party survive what's left of this dungeon?" requires.
        carry_over = (options or {}).get("carry_over")

        self._recovery_turns_used = 0
        self.rng = SeededRNG(seed)
        self.engine = CombatEngine(self.store, self.rng)

        hero_defs = [self.store.heroes[hid] for hid in hero_ids]
        enemy_defs = [self.store.enemies[eid] for eid in enemy_ids]
        self.engine.reset(hero_defs, enemy_defs, torch_level=torch_level, hero_levels=hero_levels,
                           hero_skill_ids=hero_skill_ids, provisions=provisions,
                           carry_over=carry_over,
                           # **The underleveled-hero penalty, finally reachable.**
                           # `None` here keeps the bare env identical to before:
                           # a caller with no quest has no difficulty to be
                           # under. The expedition envs set it from the tier
                           # they sampled -- see `TIER_TO_QUEST_DIFFICULTY`.
                           quest_difficulty_level=(options or {}).get(
                               "quest_difficulty_level",
                               getattr(self, "_quest_difficulty_level", None)),
                           # Empty unless a caller names the region. An unstated
                           # zone grants `in_dungeon` buffs exactly as they were
                           # granted before the gate existed, so every existing
                           # call site is byte-identical.
                           zone_id=zone_id,
                           on_board_ready=self._on_board_ready)
        self._rebuild_action_table()

        return self._get_obs(), {}

    def _on_board_ready(self):
        """Called by `CombatEngine.reset()` once the board is built and BEFORE
        it rolls surprise, computes initiative or lets any enemy act.

        A no-op here — the base env fields heroes exactly as their resolve
        level describes them. Subclasses that give a hero anything persistent
        (gear ranks, trinkets, quirks) MUST do it here rather than after
        `reset()` returns, or the opening round is fought by an unequipped
        party. See the comment at the call site.
        """

    # ── Reward shaping ──
    #
    # Potential-based shaping: reward = γ·Φ(s') − Φ(s) + turn_penalty, plus a
    # terminal win/lose bonus. Φ is a pure function of state (mean per-side
    # score), so shaping only redistributes signal in time — it doesn't
    # change which trajectory is optimal. Lives entirely in the Gym wrapper;
    # CombatEngine stays reward-agnostic (StepResult.reward is unused, same
    # as before this change).

    def _hero_score(self, h: CombatantState) -> float:
        if h.is_dead:
            return HERO_DEAD_SCORE
        hp_frac = _clamp01(h.hp / max(h.max_hp, 1))
        stress_frac = _clamp01(h.stress / 100.0)
        # Use h.hp, not h.is_deaths_door: the flag never clears on heal
        # (engine bug, see ASSUMPTIONS.md), so it would permanently penalize
        # a hero who was healed back up after being downed once.
        dd = 1.0 if h.hp <= 0 else 0.0
        return (
            hp_frac
            - W_STRESS * stress_frac
            - W_DEATHS_DOOR * dd
            - W_AFFLICTED * (1.0 if h.is_afflicted else 0.0)
            + W_VIRTUOUS * (1.0 if h.is_virtuous else 0.0)
            # Prevention beats healing: being guarded or holding a riposte
            # stops damage arriving instead of restoring it afterwards.
            + W_HERO_GUARDED * (1.0 if h.guard_source_id is not None else 0.0)
            + W_HERO_RIPOSTE * (1.0 if h.active_riposte is not None else 0.0)
        )

    def _enemy_score(self, e: CombatantState) -> float:
        if e.is_dead:
            return ENEMY_DEAD_SCORE
        # Enemy potential is SUBTRACTED in _compute_potential, so a lower
        # score is better for the party — a stunned enemy is one fewer thing
        # acting against you, the core action-economy play of the threat phase.
        dot = 0.0
        if self._prevention_reward:
            # Capped at current HP: overkill is not extra progress, and the
            # cap is what stops this being double counted as the DoT ticks.
            dot = W_ENEMY_DOT * _clamp01(min(_outstanding_dot(e), e.hp)
                                         / max(e.max_hp, 1))
        return (_clamp01(e.hp / max(e.max_hp, 1))
                - W_ENEMY_STUNNED * (1.0 if e.is_stunned else 0.0)
                - dot)

    def _total_party_stress(self) -> float:
        """Summed stress across living heroes. Dead heroes are excluded so a
        death doesn't register as a stress *drop* and hand back reward — the
        same perverse-gradient trap that _compute_expedition_potential had to
        be fixed for."""
        return float(sum(h.stress for h in self.engine.heroes if h.is_alive()))

    def _living_enemy_count(self) -> int:
        return sum(1 for e in self.engine.enemies if e.is_alive())

    def _party_needs_recovery(self) -> bool:
        return any(
            h.hp < RECOVERY_HP_FRACTION * max(h.max_hp, 1) or h.stress > RECOVERY_STRESS_FLOOR
            for h in self.engine.heroes if h.is_alive()
        )

    def is_recovery_phase(self) -> bool:
        """True when deliberately slowing down is correct play: the fight is
        down to the last enemy, the party has something worth recovering, and
        the GAME is not yet charging for the delay.

        All three matter. The enemy count reverts the phase when
        reinforcements arrive mid-stall; the need-gate stops a topped-up
        party dawdling; and `_stall_is_still_cheap` replaces the invented
        per-combat budget — see the constants block for the measurement that
        removed it."""
        return (self._living_enemy_count() <= STALL_PHASE_MAX_ENEMIES
                and self._party_needs_recovery()
                and self._stall_is_still_cheap())

    def _stall_is_still_cheap(self) -> bool:
        """Is the game itself still letting the party stand around for free?

        Two ways it is. Either the stall counter has not yet reached the round
        where `rules.json` starts charging party stress for it, or the
        reinforcement wave has ALREADY arrived — it comes once, so after it
        there is nothing further to be charged and the flag is already an
        observation feature for exactly that reason.

        What this deliberately does NOT do is bound the total. Fran's
        technique resets the counter every round by landing two invalidating
        skills, and that is the real game: `combat_engine` implements the
        reset, the thresholds and the accelerated increments. A second,
        invented bound on top is what made the play unlearnable.
        """
        if self.engine._reinforcements_spawned:
            return True
        return (self.engine.stall_counter
                < self.engine.rules.stall_round_stress_effect_threshold)

    def _blocking_corpses(self) -> int:
        """Corpses with a living enemy behind them — see W_BLOCKING_CORPSE."""
        enemies = self.engine.enemies
        return sum(
            1 for c in enemies
            if c.is_corpse and any(e.is_alive() and e.position > c.position for e in enemies)
        )

    def _compute_potential(self) -> float:
        heroes = self.engine.heroes
        enemies = self.engine.enemies
        hero_potential = sum(self._hero_score(h) for h in heroes) / max(len(heroes), 1)
        enemy_potential = sum(self._enemy_score(e) for e in enemies) / max(len(enemies), 1)
        blocking = W_BLOCKING_CORPSE * self._blocking_corpses() / RANKS
        return (hero_potential - enemy_potential - blocking
                - self._incoming_threat_penalty())

    def _incoming_threat_penalty(self) -> float:
        """What the board is about to do to the party, as a cost inside Φ.

        Normalised against the party's own remaining HP and against the same
        40-point stress scale `_get_obs` uses, so the term is bounded and a
        four-hero party is not penalised four times for one board.

        **It draws nothing.** `threat_forecast` is the weighting half of the
        monster-brain selectors with no RNG in it -- that is the contract
        `tests/engine/test_threat_forecast.py` enforces by replaying seeded episodes
        with and without calling it, and the reward calling it must not break
        that.
        """
        if not self._prevention_reward or self.engine is None:
            return 0.0
        forecast = self.engine.threat_forecast()
        if not forecast:
            return 0.0
        living_hp = sum(h.hp for h in self.engine.heroes if h.is_alive())
        damage = sum(v["damage"] for v in forecast.values())
        stress = sum(v["stress"] for v in forecast.values())
        return (W_INCOMING_DAMAGE * _clamp01(damage / max(living_hp, 1.0))
                + W_INCOMING_STRESS * _clamp01(stress / 40.0))

    def _is_episode_terminal(self) -> bool:
        """Does the current `engine.done` also end the RL EPISODE?

        For a single combat the two are the same thing, so this is just
        `engine.done` here. `ExpeditionSequenceEnv` overrides it, and that
        override is the whole point of the method existing.

        Why it exists (2026-08-23). `phi_after` used to be pinned to 0.0 on
        `engine.done` unconditionally, and in an expedition that fires at
        EVERY combat end -- including the internal transitions the subclass
        immediately un-terminates. Pinning the endpoint makes one combat's
        shaping telescope to

            g*0 - phi_start + (g-1)*sum(intermediate)  ~=  -phi_start

        which has two consequences, and the second is the worse one. A party
        that walks into a fight healthier is charged MORE (about -0.33 per
        boundary, -4.9 over a long expedition). And because the endpoint is
        pinned, NOTHING that happens inside the combat reaches its shaping
        total: the HP term, the stress term, guard, riposte and
        `W_ENEMY_STUNNED` all cancel out over the fight and only `phi_start`
        survives, as a penalty. The whole potential function was being washed
        out once per combat.

        It also broke the invariance argument this file rests on. `CLAUDE.md`
        names exactly TWO deliberate non-potential terms; this was a third
        that nobody declared. The expedition-level potential already had it
        right -- it zeroes only at the true end -- so the per-combat one was
        the odd one out rather than the precedent.
        """
        return self.engine.done

    # ── The observation `step` hands back ───────────────────────────────
    #
    # `_get_obs` is 76% of an env step (measured 2026-08-27: 279 us of a 736 us
    # step, of which the threat forecast alone is 119 us), and a subclass whose
    # own `step` rebuilds it before returning was paying that TWICE per step
    # for one usable vector. `ExpeditionSequenceEnv` is exactly that shape: it
    # calls `super().step()`, then mutates the world (combat transitions, the
    # roster snapshot, a retreat re-entering the same room) and returns a
    # freshly built observation, so the base's copy was not merely redundant --
    # in a transition it was STALE, and discarding it was already the correct
    # behaviour.
    #
    # A subclass that overrides `step` and always returns its own `_get_obs()`
    # sets this False. Nothing else may: the value handed back here is a shared
    # zero vector, and returning that to a caller who reads it is silent
    # nonsense rather than an error. `tests/test_obs_build_once.py` pins both
    # halves -- that the flag is False only on a class overriding `step`, and
    # that the observation the subclass returns is byte-identical to the one
    # the double build produced.
    _BASE_STEP_RETURNS_OBS = True

    def _step_obs(self) -> np.ndarray:
        """The observation `step` returns -- real, or a discarded placeholder.

        See `_BASE_STEP_RETURNS_OBS`.
        """
        if self._BASE_STEP_RETURNS_OBS:
            return self._get_obs()
        buf = getattr(self, "_discarded_obs_buf", None)
        if buf is None:
            buf = np.zeros(self.observation_space.shape, dtype=np.float32)
            self._discarded_obs_buf = buf
        return buf

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
        if self.engine.done:
            return self._step_obs(), 0.0, True, False, {"winner": self.engine.winner}

        current_hero = self.engine.get_current_actor_id()
        if current_hero is not None:
            # Stable lookup, no clamping. The old code did
            # `min(action, len(valid_actions)-1)`, which silently rewrote an
            # out-of-range choice into whatever happened to be last in the
            # list — the source of the "shuffles position forever" pathology.
            # With fixed semantics an unmapped index simply isn't legal, and
            # the mask should already have prevented it; falling back to the
            # engine's own last-resort __pass__ keeps that honest instead of
            # substituting a different real action the policy didn't pick.
            mapped = self._action_map.get(action)
            if mapped is None:
                mapped = Action(current_hero, "__pass__", -1)
            self.engine.submit_action(mapped)

        phi_before = self._compute_potential()
        # Phase is read BEFORE stepping, so the turn is charged at the rate of
        # the situation the decision was actually made in — a turn spent
        # healing while three enemies were still up is a threat-phase turn
        # even if the step happens to end the fight.
        in_recovery = self.is_recovery_phase()
        if in_recovery:
            self._recovery_turns_used += 1
        turn_penalty = TURN_PENALTY_STALL if in_recovery else TURN_PENALTY_THREAT
        stress_before = self._total_party_stress()
        result = self.engine.step()
        self._rebuild_action_table()

        phi_after = 0.0 if self._is_episode_terminal() else self._compute_potential()
        shaping = SHAPING_GAMMA * phi_after - phi_before
        # Dense stress cost, charged when the stress actually lands. Only
        # increases: see STRESS_TICK_PENALTY.
        stress_gained = max(0.0, self._total_party_stress() - stress_before)
        reward = (shaping + turn_penalty
                  - self.stress_weight * STRESS_TICK_PENALTY * stress_gained)
        if self.engine.done:
            # Three outcomes now, not two. A retreat is neither: nobody died,
            # nothing was cleared, and the party paid `combat_retreat_stress`
            # per living hero — which this env already charges, densely, through
            # STRESS_TICK_PENALTY on the step the stress lands. Adding a
            # terminal bonus or penalty on top would be pricing the same event
            # twice, so REWARD_RETREAT is 0.0 and the stress IS the bill.
            if self.engine.winner == "retreat":
                reward += self._reward_retreat
            else:
                reward += (self.win_reward() if self.engine.winner == "heroes"
                           else self._reward_lose)

        return self._step_obs(), reward, self.engine.done, False, {"winner": self.engine.winner}

    def _can_abandon(self) -> bool:
        """Whether ACTION_ABANDON is offered. False here: a single combat is
        not an expedition, so there is nothing to abandon. Overridden by
        `ExpeditionSequenceEnv`."""
        return False

    def win_reward(self) -> float:
        """What clearing this combat is worth.

        A HOOK, not a constant, so a subclass can price the STATE the party
        finishes in rather than only the fact that it won. `ExpeditionSequenceEnv`
        overrides it; here it is the flat `_reward_win` it has always been, so
        `DarkestDungeonEnv` and `ExpeditionEnv` are unchanged.
        """
        return self._reward_win

    def action_masks(self) -> np.ndarray:
        """Boolean mask over the FIXED-SEMANTICS action space (see the layout
        comment at the top of this module): True exactly for the indices that
        map to a currently-legal Action for the current actor.

        Masking remains mandatory, for the same reason as before: step() has
        to do something with an index that doesn't map to a legal action, and
        anything it does is a silent lie about what the policy chose. What
        changed is that an index now has a stable meaning, so the mask is a
        statement about the game state ("skill slot 2 can hit rank 1 right
        now") rather than about the length of a volatile list."""
        mask = np.zeros(self.action_space.n, dtype=bool)
        for idx in self._action_map:
            mask[idx] = True
        # Abandoning is a decision about the EXPEDITION, not about this board,
        # so it is legal whenever a hero has a turn rather than being derived
        # from `_action_map` (which only ever holds engine Actions).
        if self._can_abandon():
            mask[ACTION_ABANDON] = True
        if not mask.any():
            # No legal action at all (combat over / no living actor). Leave a
            # single index open so MaskablePPO's categorical distribution
            # stays well-defined; step() no-ops on it.
            mask[ACTION_PASS] = True
        return mask

    def _action_index(self, action: Action, slot_of: dict[str, int]) -> Optional[int]:
        """Concrete engine Action -> fixed action-space index, or None if it
        can't be represented (e.g. a skill outside the hero's 7 slots)."""
        sid = action.skill_id
        if sid == "__pass__":
            return ACTION_PASS
        if sid == "__retreat__":
            return ACTION_RETREAT
        if sid == "__move__":
            return ACTION_MOVE_BASE + action.target_id
        if sid.startswith("__provision_"):
            pid = sid[len("__provision_"):-2]
            if pid in PROVISION_ACTION_ORDER:
                return ACTION_PROVISION_BASE + PROVISION_ACTION_ORDER.index(pid)
            return None
        slot = slot_of.get(sid)
        if slot is None or slot >= MAX_SKILL_SLOTS:
            return None
        # A skill with no target list (self-only) is offered by the engine
        # with target_id == -1; it collapses onto its slot's rank-0 index,
        # which no real targeted use of that same slot can also occupy.
        rank = action.target_id if action.target_id >= 0 else 0
        if not (0 <= rank < RANKS):
            return None
        return slot * RANKS + rank

    def _slot_order(self, hero_id: int) -> list[str]:
        """The hero's skills in ACTION-SLOT order — the single source for both
        the action map and the observation's skill block.

        These were two independent `enumerate(get_hero_full_skill_ids(...))`
        calls, coupled only by both happening to call the same engine method.
        That is a silent-failure shape: permute or reorder one and not the
        other and the policy reads slot k's features while index `k*RANKS + r`
        fires slot j's skill, and **the action mask cannot catch it** — the
        mask is built from `_action_map` alone, so it stays internally
        consistent while describing the wrong skill. Hoisted here so there is
        one place to change and one place to permute.
        """
        full = self.engine.get_hero_full_skill_ids(hero_id)
        if self._slot_perm_rng is None or not full:
            return full
        # `--slot-permute`: shuffle which skill sits at which action index,
        # fixed for the whole episode and applied to BOTH consumers. See
        # `_reset_slot_permutation`.
        perm = self._slot_perms.get(hero_id)
        if perm is None or len(perm) != len(full):
            perm = list(range(len(full)))
            self._slot_perm_rng.shuffle(perm)
            self._slot_perms[hero_id] = perm
        return [full[i] for i in perm]

    def _reset_slot_permutation(self) -> None:
        """Draw a fresh slot permutation per EPISODE, not per step.

        Per step would make the action space non-stationary within a single
        decision sequence, which is a different (and incoherent) experiment.
        Per episode is the augmentation: across training the policy sees every
        skill at every index, so "the slot that heals" cannot be memorised as
        an index and has to be read from the slot's own payload features.
        """
        self._slot_perms = {}

    def _rebuild_action_table(self):
        hero = self.engine.get_current_actor_id()
        self._action_map = {}
        if hero is None:
            self._valid_actions = []
            return
        self._valid_actions = self.engine.get_valid_actions(hero)
        slot_of = {sid: i for i, sid in enumerate(self._slot_order(hero))}
        for a in self._valid_actions:
            idx = self._action_index(a, slot_of)
            # First writer wins: an AoE skill is offered once with a
            # representative target, so there's no meaningful collision, and
            # for anything else distinct legal actions map to distinct indices.
            if idx is not None and idx not in self._action_map:
                self._action_map[idx] = a

    @property
    def base_obs_size(self) -> int:
        return (
            RANKS * HERO_SLOT_SCALARS
            + RANKS * (ENEMY_SLOT_SCALARS + len(self._enemy_type_index))
            + MAX_SKILL_SLOTS * SKILL_SLOT_FEATURES
            + GLOBAL_FEATURES
        )

    @property
    def skill_block_slice(self) -> slice:
        """Where the ACTING hero's seven skill-slot vectors sit in the
        observation, as a slice.

        For a policy head that scores an action from what the skill DOES rather
        than from which index it occupies -- the slot block is
        `MAX_SKILL_SLOTS x SKILL_SLOT_FEATURES` and lines up one-for-one with
        action indices `0 .. MAX_SKILL_SLOTS * RANKS - 1`, because
        `_skill_slot_vecs` and `_rebuild_action_table` both read `_slot_order`.
        That shared order is what makes the slice meaningful, and it is the
        thing `--slot-permute` was refactored to guarantee.

        DERIVED, never written down. Three pinned observation offsets broke on
        2026-08-25 alone -- `[-1]` for the heal headroom, a literal `-8` for the
        retreat chance, and `[-3]` for the party forecast, which forgot that
        `ExpeditionSequenceEnv` appends its own context features AFTER the base
        globals. The enemy-type one-hot width is a property of the loaded store,
        so this cannot be a module constant either.
        """
        start = (RANKS * HERO_SLOT_SCALARS
                 + RANKS * (ENEMY_SLOT_SCALARS + len(self._enemy_type_index)))
        return slice(start, start + MAX_SKILL_SLOTS * SKILL_SLOT_FEATURES)

    def _turn_order_features(self, actor_id) -> list:
        """Where the acting hero sits in the initiative queue, and how many
        enemy turns come before the next hero acts.

        Two floats, because those are the two questions the queue is actually
        asked: "how much of the round is left for me" and "how much damage
        arrives before my side moves again". Reconstructing either from the
        board is impossible -- speed is in the observation, but the queue is
        SPEED + d8 rolled at reset, so the order is not a function of the
        visible stats.
        """
        order = getattr(self.engine, "turn_order", None) or []
        idx = getattr(self.engine, "current_turn_index", 0)
        if not order:
            return [0.0, 0.0]
        position = _clamp01(idx / max(len(order), 1))
        enemies_first = 0
        # `turn_order` holds CombatantState OBJECTS, not ids. The first version
        # of this looked each entry up by `x.id == entry`, which is False for
        # every entry, so the second feature was a CONSTANT 0.0 -- a dead
        # input the policy could learn nothing from and nothing would flag.
        # tests/env/test_turn_order_obs.py pins it against exactly that.
        for c in order[idx + 1:]:
            if not c.is_alive():
                continue
            if c.is_hero:
                break
            enemies_first += 1
        return [position, _clamp01(enemies_first / RANKS)]

    def _hero_capabilities(self, h) -> list:
        """What this hero CAN DO, max-pooled over the skills it actually has.

        The mirror of `_enemy_capabilities`, and the repair for the failed
        transfer falsifier: without it the policy reads three allied ranks as
        bare stat blocks, cannot tell a Vestal from a Leper, and so cannot
        learn "the rank behind me can heal" as a property of the BOARD rather
        than of a class index.

        Read from the RESTRICTED loadout, not the class's full seven: two
        Vestals running different four-skill kits genuinely differ in what they
        can do, and `--loadout-random-p` varies exactly that. The unrestricted
        classes (Abomination, Duelist) carry no restriction, so
        `get_hero_skill_ids` already returns all seven -- correct, since for
        them all seven really are equipped.

        Max-pooled by magnitude rather than summed, same as the enemy side: the
        question is "can this ally stun", not "how much stun do they own".

        THREE pools -- ALLY, SELF, FOE -- for the same reason the stat buckets
        inside each are recorded once per side: pooled into one vector, a
        Vestal's Divine Grace and a Leper's Solemnity both read as "heal 0.3",
        and the fact the policy actually needs -- that the rank behind it can
        heal IT -- is not recoverable. Asking it to infer that from an XOR
        against some other feature is exactly what `STAT_BUCKETS` is doubled to
        avoid.

        The split reads two fields, and BOTH are needed. `targets_allies` is
        the `@` prefix on `.target`, so it is True only for skills that can be
        aimed at another rank of the party. A SELF-only skill (Leper's
        Solemnity, Houndmaster's Lick Wounds, Leper's Revenge and Withstand)
        carries `targets_allies False` with an EMPTY `.target`, so keying on
        the flag alone files it under FOE -- which claims the hero can heal the
        enemy. The empty target list is what separates them.

        A heal in the FOE pool is therefore a LIFESTEAL, not a mistake: the
        Vestal's Judgement is aimed at an enemy and heals her. The payload
        indexes carry a side for stat buckets (`T_` / `S_`) but not for heals,
        so which pool it lands in is the only thing saying who gets the HP.
        """
        ids = tuple(self.engine.get_hero_skill_ids(h.id))
        blank = [0.0] * (3 * SKILL_MECHANIC_FEATURES)
        if not ids:
            return blank
        key = (ids, h.skill_tier)
        hit = self._hero_caps_memo.get(key)
        if hit is not None:
            return hit
        ally = [0.0] * SKILL_MECHANIC_FEATURES
        me = [0.0] * SKILL_MECHANIC_FEATURES
        foe = [0.0] * SKILL_MECHANIC_FEATURES
        for sid in ids:
            sd = self.engine.get_skill_data(sid, level=h.skill_tier)
            if sd is None:
                continue
            v = self._skill_mechanics(sid, h.skill_tier)
            side = ally if sd.targets_allies else (foe if sd.target else me)
            side[:] = [max(a, b, key=abs) for a, b in zip(side, v)]
        caps = ally + me + foe
        self._hero_caps_memo[key] = caps
        return caps

    def _enemy_capabilities(self, definition_id: str) -> list:
        """What this monster CAN DO, as the same payload vector a hero skill
        gets, max-pooled over its whole moveset.

        The largest hole the 2026-08-23 audit found: of 22 `EnemyDefinition`
        fields the observation read TWO, so the policy was fighting monsters
        whose movesets it could not see. It knew a rank-2 enemy had 14 HP and
        hit for 8, and had no way to know whether it stuns, bleeds, marks,
        heals its friends or shuffles the party.

        Max-pooled rather than summed: the question is "can this thing stun
        me", not "how much stun does it own". Read from
        `EnemyDefinition.skills` DIRECTLY, never through
        `engine.get_skill_data` -- that goes via `DataStore.skill_index()`,
        which is built heroes-first with `setdefault`, so a monster skill whose
        id collides with a hero's would silently resolve to the hero's numbers.
        """
        memo = self._enemy_caps_memo
        hit = memo.get(definition_id)
        if hit is not None:
            return hit
        caps = [0.0] * SKILL_MECHANIC_FEATURES
        ed = self.store.enemies.get(definition_id)
        if ed is not None:
            for sk in ed.skills:
                if not sk.levels:
                    continue
                data = sk.levels[max(sk.levels)]
                v = self._mechanics_from_data(data)
                caps = [max(a, b, key=abs) for a, b in zip(caps, v)]
        memo[definition_id] = caps
        return caps

    def _threat_ratio(self) -> float:
        """Enemy damage per round / party HP remaining, clamped to [0, 1].

        Deliberately crude: mean damage times actions per round, against raw
        HP. It ignores dodge, prot, guard and who the AI would actually pick,
        because a precise answer needs the monster brains and this only has to
        separate "we can stand here" from "we cannot". A stunned enemy is
        excluded -- it does not act this round, which is the entire reason
        stunning buys a stall turn.
        """
        incoming = 0.0
        for e in self.engine.enemies:
            if not e.is_alive() or e.is_stunned:
                continue
            ed = self.store.enemies.get(e.definition_id)
            per_round = max(1, ed.actions_per_round if ed else 1)
            incoming += ((e.dmg_min or 0) + (e.dmg_max or 0)) / 2.0 * per_round
        party_hp = sum(h.hp for h in self.engine.heroes if h.is_alive())
        return _clamp01(incoming / max(party_hp, 1.0))

    def _heal_throughput(self) -> float:
        """Party HP per round if every living hero spent its turn healing.

        The denominator of the SECURED test. Reads the RESTRICTED 4-of-7
        loadout, never the full movepool: a Vestal who did not bring a heal is
        not a healer, and `--loadout-random-p` varies exactly that. Same rule
        `_hero_capabilities` follows, for the same reason.

        An ally-or-self heal only. A lifesteal aimed at an enemy heals the
        attacker and is real, but it is not throughput the party can point at
        whoever is dying, which is what "can we heal through this" means.
        """
        total = 0.0
        for h in self.engine.heroes:
            if not h.is_alive():
                continue
            best = 0.0
            for sid in self.engine.get_hero_skill_ids(h.id):
                sd = self.engine.get_skill_data(sid, level=h.skill_tier)
                if sd is None or sd.heal_min is None:
                    continue
                if not self.engine._is_ally_skill(sid):
                    continue
                best = max(best, (float(sd.heal_min) + float(sd.heal_max)) / 2.0)
            total += best
        return total

    def _hero_at_rank(self, rank: int) -> Optional[CombatantState]:
        # Heroes leave no corpse (see _collapse_hero_position_gap), so a rank
        # is occupied only by a living hero.
        for h in self.engine.heroes:
            if h.is_alive() and h.position == rank:
                return h
        return None

    def _enemy_at_rank(self, rank: int) -> Optional[CombatantState]:
        # Unlike heroes, a dead enemy can leave a corpse that keeps blocking
        # its rank(s) until cleared, so corpses are reported here (flagged as
        # such) rather than treated as an empty rank. Size >1 enemies occupy
        # several consecutive ranks.
        corpse = None
        for e in self.engine.enemies:
            if not (e.position <= rank < e.position + max(1, e.size)):
                continue
            if e.is_alive():
                return e
            if e.is_corpse and corpse is None:
                corpse = e
        return corpse

    def _hero_slot_vec(self, h: Optional[CombatantState], is_actor: bool,
                       forecast: Optional[dict] = None) -> list[float]:
        """A hero rank, as STATE only -- no class identity.

        The 20-class one-hot was removed on 2026-08-23 (Fran's call). It was
        what let the policy memorise "Vestal slot 3 is good here" instead of
        learning that a heal is good here, and a modded class has no slot in it
        at all, so nothing transferred. What a hero can DO now reaches the
        policy through the acting hero's skill payloads instead, which are
        class-agnostic by construction.

        That cost was predicted here and then MEASURED: dropping the one-hot
        cost +46.9pp of excess gap on a held-out class (CI [+37.2, +56.9], 17
        of 17 comps worse, arms level on seen comps). The fix named here -- a
        capability profile, not identity -- is `_hero_capabilities`, appended
        at the end of this vector.
        """
        if h is None:
            return [0.0] * HERO_SLOT_SCALARS
        # Passed in rather than computed here: it is ONE call for the whole
        # board and this runs four times a step.
        f = (forecast or {}).get(h.id) or {}
        inc_dmg = float(f.get("damage", 0.0))
        return [
            1.0,                                       # rank occupied
            1.0 if is_actor else 0.0,                  # whose turn it is
            _clamp01(h.hp / max(h.max_hp, 1)),
            _clamp01(h.stress / 200.0),                # /200: the heart-attack ceiling
            1.0 if h.is_deaths_door else 0.0,
            1.0 if h.is_stunned else 0.0,
            1.0 if h.is_marked else 0.0,
            1.0 if h.guard_source_id is not None else 0.0,   # being guarded
            1.0 if h.guard_target_id is not None else 0.0,   # guarding someone
            1.0 if h.active_riposte is not None else 0.0,
            1.0 if h.is_stealthed else 0.0,
            1.0 if h.is_afflicted else 0.0,
            1.0 if h.is_virtuous else 0.0,
            _dot_total(h.bleeds),
            _dot_total(h.blights),
            _dot_total(h.stress_dots),
            _clamp01((h.damage_blocks or 0) / 2.0),           # Shieldbreaker Aegis
            # Live stats, so buff/debuff effects are observable without
            # enumerating buff ids — this is how conditional trinket/quirk
            # buffs and skill debuffs reach the policy.
            _clamp01(h.dodge),
            _clamp01(h.prot),
            _clamp01(h.speed / 15.0),
            _clamp01(h.crit),
            _burn_total(h),                            # appended, see _enemy_slot_vec
            # --- APPENDED 2026-08-23, after the held-out-Duelist run came back
            # +8.2pp AGAINST the mechanic encoding and an audit of
            # `CombatantState` against this vector found the reason was not
            # (only) the encoding: seventeen decision-relevant fields were not
            # in here at all.
            #
            # THE SIX RESISTS. Nothing on either side of the board carried
            # them, so "this enemy resists stun" was unobservable -- which is
            # the whole of Fran's Blinding Gas example (140% stun against 90
            # stun resist is a 50% landing, and the trinket that buys the next
            # 20 is only worth it against a target the first 140 does not
            # already cover). A policy that cannot see a resist cannot learn
            # when a status skill is wasted.
            _clamp01(h.stun_resist),
            _clamp01(h.blight_resist),
            _clamp01(h.bleed_resist),
            _clamp01(h.debuff_resist),
            _clamp01(h.move_resist),
            _clamp01(h.death_blow_resist),
            # What this hero HITS FOR, and how accurately. Absent until now, so
            # the policy could not compare its own heroes' damage at all -- the
            # skill payload says `dmg_mod`, a MULTIPLIER, with nothing to
            # multiply.
            _clamp01(((h.dmg_min or 0) + (h.dmg_max or 0)) / 2.0 / 30.0),
            _clamp01((h.atk_bonus or 0) + 0.5),               # signed, recentred
            # STANCE. `CombatantState.mode` appeared nowhere, and the Duelist
            # -- one of two stance heroes -- was the class held out in the
            # transfer run. Removing the class one-hot without this made a
            # stance hero STRICTLY less observable than before, which is a
            # regression the run was probably measuring.
            # Two features rather than a one-hot so a modded class with three
            # modes still encodes: does it have stances at all, and which.
            _hero_has_modes(self.store, h),
            _hero_mode_index(self.store, h),
            # Death's Door has THREE tiers plus the heart attack, and only the
            # bool was here -- so "one more hit kills this hero" read the same
            # as "just entered Death's Door".
            # `mortality_tier` is a STRING ("none"/"mortality"/"heart_attack"),
            # not a number -- `MORTALITY_TIER_RANK` is the engine's own ladder
            # and is the single place that ordering is defined.
            _clamp01(MORTALITY_TIER_RANK.get(h.mortality_tier, 0) / 2.0),
            1.0 if h.next_recovery_is_heart_attack else 0.0,
            # Resolve level drives resists and gear, and is what separates an
            # Apprentice party from a Champion one. It was not in here.
            _clamp01((h.level or 0) / 6.0),
            # DURATIONS. The bools above say a status is present; these say for
            # how long, which is what decides whether it is worth acting on.
            _clamp01((h.mark_rounds_remaining or 0) / 4.0),
            _clamp01((h.stealth_rounds_remaining or 0) / 4.0),
            _clamp01((h.guard_rounds_remaining or 0) / 4.0),
            _dot_total(h.heal_dots),
            # --- APPENDED 2026-08-23 (third batch). A buff whose stat_type is
            # NOT one of the live stats above lands in a dedicated field on
            # `CombatantState` and was invisible: the policy could see a +DODGE
            # buff (because dodge is read live) but not a "+20% stun chance"
            # one, so half of `status_chance` was observable from the SKILL
            # side and none of it from the CARRIER side.
            #
            # Multipliers are recentred (1.0 -> 0.5) so "unmodified" is not
            # confusable with "zeroed".
            _clamp01(h.stress_dmg_received_mult / 2.0),
            _clamp01(h.hp_heal_received_mult / 2.0),
            _clamp01(h.hp_heal_produced_mult / 2.0),
            _clamp01(h.stress_heal_received_mult / 2.0),
            _clamp01(h.stress_heal_produced_mult / 2.0),
            _clamp01(h.crit_received_bonus + 0.5),
            _clamp01(h.resolve_check_mod + 0.5),
            _clamp01(h.stun_chance_bonus + 0.5),
            _clamp01(h.bleed_chance_bonus + 0.5),
            _clamp01(h.blight_chance_bonus + 0.5),
            _clamp01(h.move_chance_bonus + 0.5),
            _clamp01(h.debuff_chance_bonus + 0.5),
            _clamp01(h.burn_amount_bonus + 0.5),
            _clamp01(h.burn_amount_received_bonus + 0.5),
            _clamp01(h.burn_decay_bonus + 0.5),
            # HOW MANY modifiers are riding on this hero and how soon the
            # nearest expires. The live stats say what the total currently is;
            # these say whether it is about to stop being true, which is what
            # decides whether to spend a turn now or next round.
            _clamp01(len(h.buffs) / 8.0),
            _buff_min_duration(h),
            # --- APPENDED 2026-08-23: what this rank CAN DO. See
            # `_hero_capabilities` and the HERO_SLOT_SCALARS comment.
            *self._hero_capabilities(h),
            # --- APPENDED 2026-08-24: absolute HP headroom, /30 to match the
            # skill payload's heal scale. A hero on Death's Door reads FULL
            # headroom whatever the HP says, the same exception
            # `_is_wasted_heal` already makes -- healing them off the door is
            # never wasted however few points it restores.
            (1.0 if h.is_deaths_door
             else _clamp01((h.max_hp - h.hp) / 30.0)),
            # --- APPENDED 2026-08-24: the threat forecast. See
            # HERO_THREAT_FORECAST for what each one is and why.
            _clamp01(inc_dmg / max(h.hp, 1.0)),
            _clamp01(inc_dmg / max(h.max_hp, 1.0)),
            _clamp01(float(f.get("stress", 0.0)) / 20.0),
            _clamp01(float(f.get("p_targeted", 0.0))),
        ]

    def _enemy_slot_vec(self, e: Optional[CombatantState]) -> list[float]:
        n_types = len(self._enemy_type_index)
        if e is None:
            return [0.0] * (ENEMY_SLOT_SCALARS + n_types)
        types = [0.0] * n_types
        ed = self.store.enemies.get(e.definition_id)
        if ed:
            for t in ed.enemy_types:
                i = self._enemy_type_index.get(t)
                if i is not None:
                    types[i] = 1.0
        return [
            1.0,                                       # rank occupied
            _clamp01(e.hp / max(e.max_hp, 1)),
            1.0 if e.is_corpse else 0.0,
            1.0 if e.is_stunned else 0.0,
            1.0 if e.is_marked else 0.0,
            1.0 if e.is_stealthed else 0.0,
            _dot_total(e.bleeds),
            _dot_total(e.blights),
            1.0 if _is_doomed(e) else 0.0,
            _clamp01(e.size / 4.0),
            _clamp01(e.dodge),
            _clamp01(e.prot),
            _clamp01(e.speed / 15.0),
            _clamp01(e.crit),
            _clamp01((ed.actions_per_round if ed else 1) / 3.0),
            # APPENDED, not inserted beside the other two DoTs. Every
            # index below this one still means what it meant -- the same
            # discipline the retreat action was added under, and the
            # reason a test pinning `doomed` at offset 8 still passes.
            _burn_total(e),
            # --- APPENDED 2026-08-23, same audit as the hero slot. The resists
            # matter MORE on this side: they are what decides whether a stun,
            # a debuff or a DoT is worth the turn.
            _clamp01(e.stun_resist),
            _clamp01(e.blight_resist),
            _clamp01(e.bleed_resist),
            _clamp01(e.debuff_resist),
            _clamp01(e.move_resist),
            _clamp01(e.death_blow_resist),
            # How hard this specific enemy hits. `_threat_ratio` aggregates it
            # into one global, which tells the policy the board is dangerous
            # but not WHICH rank is the danger -- so "kill the dangerous one
            # first", the whole of PLAYBOOK 3.1, was not expressible.
            _clamp01(((e.dmg_min or 0) + (e.dmg_max or 0)) / 2.0 / 30.0),
            _clamp01((e.atk_bonus or 0) + 0.5),               # signed, recentred
            _clamp01((e.mark_rounds_remaining or 0) / 4.0),
            _clamp01((e.stealth_rounds_remaining or 0) / 4.0),
            _clamp01((e.damage_blocks or 0) / 2.0),
            # --- APPENDED 2026-08-23 (third batch).
            # Horror. `stress_dots` was on the HERO slot only, so a stress DoT
            # riding an enemy was unobservable.
            _dot_total(e.stress_dots),
            # The damage SPREAD, not just the mean. A 2-14 swing and a flat 8
            # were the same observation, and they are not the same threat.
            _clamp01(((e.dmg_max or 0) - (e.dmg_min or 0)) / 20.0),
            # How long this corpse has before it rots on its own -- "blocks for
            # three more rounds" and "gone next round" read identically before.
            _clamp01((e.corpse_rounds_remaining or 0) / 3.0),
            # The attacker-side bonuses that decide how well THIS enemy lands
            # statuses on the party, plus how much a crit on it is worth.
            _clamp01(e.crit_received_bonus + 0.5),
            _clamp01(e.hp_heal_received_mult / 2.0),
            _clamp01(e.stun_chance_bonus + 0.5),
            _clamp01(e.bleed_chance_bonus + 0.5),
            _clamp01(e.blight_chance_bonus + 0.5),
            _clamp01(e.move_chance_bonus + 0.5),
            _clamp01(e.debuff_chance_bonus + 0.5),
            _clamp01(len(e.buffs) / 8.0),
            _buff_min_duration(e),
        ] + self._enemy_capabilities(e.definition_id) + types

    def _skill_mechanics(self, skill_id: str, level: int) -> list[float]:
        """The payload half of a skill slot: WHAT this skill does, in game
        numbers, independent of who is holding it.

        Memoised per `(skill_id, level)` because it is static -- a skill's
        payload does not depend on board state -- and this runs for 7 slots on
        every env step. Without the memo it walks the effect list and resolves
        `buff_ids` against `store.buffs` ~7x per step.

        Magnitudes are scaled into roughly [-1, 1] by dividing by a
        representative value rather than by a true maximum: the point is that
        the policy can COMPARE two skills on an axis, not that any particular
        float is a rate.
        """
        memo = self._skill_mechanics_memo
        key = (skill_id, level)
        hit = memo.get(key)
        if hit is not None:
            return hit
        v = self._mechanics_from_data(
            self.engine.get_skill_data(skill_id, level=level))
        memo[key] = v
        return v

    def _mechanics_from_data(self, data) -> list[float]:
        """The extraction itself, over a `SkillLevelData`.

        Split out of `_skill_mechanics` so `_enemy_capabilities` can reuse it.
        It must take the DATA, not an id: a monster skill is resolved from its
        own `EnemyDefinition.skills`, never through `engine.get_skill_data`,
        which goes via `DataStore.skill_index()` -- built heroes-first with
        `setdefault`, so a monster skill id colliding with a hero's silently
        returns the hero's numbers.
        """
        v = [0.0] * SKILL_MECHANIC_FEATURES
        if data is None:
            return v

        n = len(STAT_BUCKETS)
        bucket_i = {name: 18 + i for i, name in enumerate(STAT_BUCKETS)}      # to target
        self_i = {name: 18 + n + i for i, name in enumerate(STAT_BUCKETS)}    # to performer

        def stat(bucket: str, amount: float, to_self: bool):
            if bucket in _RECEIVED_IS_BAD:
                amount = -amount
            idx = (self_i if to_self else bucket_i)[bucket]
            v[idx] = max(-1.0, min(1.0, v[idx] + amount))

        named = [(n, False) for n in data.effects] + [(n, True) for n in data.self_effects]
        for name, from_self_list in named:
            eff = self.store.effects.get(name)
            if eff is None:
                continue
            # `.self_effects` always lands on the performer; a regular effect
            # can too, and says so with `target: "performer"`.
            to_self = from_self_list or eff.target == "performer"

            if eff.stun_chance is not None:
                v[0] = max(v[0], _clamp01(eff.stun_chance))
            if eff.bleed_amount:
                v[1] = _clamp01((eff.bleed_amount * (eff.bleed_rounds or 1)) / 20.0)
            if eff.blight_amount:
                v[2] = _clamp01((eff.blight_amount * (eff.blight_rounds or 1)) / 20.0)
            if eff.burn_amount:
                v[3] = _clamp01(eff.burn_amount / 10.0)
            if eff.heal_min is not None:
                v[4] = _clamp01(((eff.heal_min + (eff.heal_max or eff.heal_min)) / 2.0) / 30.0)
            if eff.heal_percent:
                v[5] = _clamp01(eff.heal_percent)
            if eff.heal_dot_amount:
                v[6] = _clamp01((eff.heal_dot_amount * (eff.heal_dot_rounds or 1)) / 20.0)
            if eff.stress_heal:
                v[7] = _clamp01(eff.stress_heal / 30.0)
            if eff.stress_damage or eff.stress_damage_per_round:
                total = (eff.stress_damage or 0) + (
                    (eff.stress_damage_per_round or 0) * (eff.stress_dot_rounds or 1))
                v[8] = _clamp01(total / 30.0)
            if eff.cures_dots or eff.cures_bleed or eff.cures_blight or eff.cures_burn:
                v[9] = 1.0
            if eff.is_guard:
                v[10] = 1.0
            if eff.is_riposte:
                v[11] = 1.0
            if eff.stealth_duration:
                v[12] = 1.0
            if eff.is_mark_effect:
                v[13] = 1.0
            if eff.shuffle_target or eff.shuffle_party:
                v[14] = 1.0
            if eff.push_distance:
                v[15] = _clamp01(eff.push_distance / 4.0)
            if eff.pull_distance:
                v[16] = _clamp01(eff.pull_distance / 4.0)
            if eff.is_kill_effect:
                v[43] = 1.0
            if eff.health_damage_blocks:
                v[44] = _clamp01(eff.health_damage_blocks / 2.0)

            # Direct stat fields on the effect record.
            for amount, bucket in (
                (eff.attack_rating_add, "acc"),
                (eff.crit_chance_add, "crit"),
                (eff.protection_rating_add, "prot"),
                (eff.defense_rating_add, "dodge"),
                (eff.speed_add, "speed"),
                (eff.max_hp_percent, "hp"),
                (eff.damage_low_add, "dmg"),
                (eff.damage_high_add, "dmg"),
            ):
                if amount:
                    stat(bucket, float(amount), to_self)
            for mult, bucket in ((eff.damage_low_multiply, "dmg"),
                                 (eff.damage_high_multiply, "dmg")):
                if mult:
                    stat(bucket, float(mult), to_self)

            # ...and the buff ids, which is where 60.7% of the signal lives.
            for bid in eff.buff_ids:
                buff = self.store.buffs.get(bid)
                if buff is None:
                    continue
                bucket = _stat_bucket(buff.stat_type, buff.stat_sub_type)
                if bucket is not None:
                    stat(bucket, float(buff.amount), to_self)

        # Skill-level fields, which are not effects at all.
        #
        # The heal AMOUNT is one of these, and missing it left the biggest
        # hole in this vector: Vestal's Divine Grace carries no healing
        # EFFECT at all, so the payload read as empty and the only thing
        # separating it from her other skills was the class one-hot that was
        # removed in the same change. `max` rather than assignment because an
        # effect-borne heal may already have written here.
        if data.heal_min is not None:
            avg = (data.heal_min + (data.heal_max or data.heal_min)) / 2.0
            v[4] = max(v[4], _clamp01(avg / 30.0))
        if data.push:
            v[15] = _clamp01(data.push / 4.0)
        if data.pull:
            v[16] = _clamp01(data.pull / 4.0)
        if data.move_self:
            v[17] = max(-1.0, min(1.0, data.move_self / 4.0))
        # NOT encoded: `SkillLevelData.extra_damage_vs_*`. All eight fields are
        # ZERO on every shipped skill -- 140 hero skills and every monster skill
        # -- so a feature for them would be permanently dead. The real "bonus
        # damage vs unholy" mechanic is delivered by buffs carrying
        # `monster_type_condition`, which reach this vector through `buff_ids`
        # like any other buff. Recorded rather than encoded; see ASSUMPTIONS.md.
        if data.per_battle_limit or data.per_turn_limit:
            v[42] = 1.0
        return v

    def _skill_slot_vecs(self, actor_id: Optional[int]) -> list[float]:
        """Per-slot features for the ACTING hero's full 7-skill movepool, in
        the same stable slot order the action space uses. Lets the policy
        reason about a slot's semantics (is it a heal? an AoE? usable right
        now?) generically across classes, instead of having to memorize a
        separate meaning for every (class, slot) pair."""
        blank = [0.0] * SKILL_SLOT_FEATURES
        if actor_id is None:
            return blank * MAX_SKILL_SLOTS
        actor = self.engine.heroes[actor_id] if actor_id < len(self.engine.heroes) else None
        full = self._slot_order(actor_id)
        if actor is None or not full:
            return blank * MAX_SKILL_SLOTS
        slot_of = {sid: i for i, sid in enumerate(full)}
        usable = {slot_of[a.skill_id] for a in self._valid_actions if a.skill_id in slot_of}

        out: list[float] = []
        for slot in range(MAX_SKILL_SLOTS):
            sd = (self.engine.get_skill_data(full[slot], level=actor.skill_tier)
                  if slot < len(full) else None)
            if sd is None:
                out.extend(blank)
                continue
            out.extend([
                1.0,                                   # slot exists
                1.0 if slot in usable else 0.0,        # legal right now
                1.0 if sd.targets_allies else 0.0,
                1.0 if sd.heal_min is not None else 0.0,
                1.0 if sd.is_aoe else 0.0,
                _clamp01(sd.atk),
                max(-1.0, min(1.0, sd.dmg_mod)),
                _clamp01(sd.crit),
                1.0 if sd.is_stall_invalidating else 0.0,
                1.0 if sd.is_continue_turn else 0.0,   # free action
                # "legal right now" collapsed three different situations into
                # one bit -- on cooldown, out of uses, and no valid target. A
                # Shieldbreaker with one Aegis left read the same as one with
                # none.
                _clamp01(actor.skill_cooldowns.get(full[slot], 0) / 3.0),
                _uses_remaining(actor, full[slot], sd),
                # REACH, which appeared nowhere: `launch` is the ranks the
                # skill can be used FROM and `target` the ranks it can hit.
                # Without them the policy learns legality only through the
                # mask, so "if I move to rank 2 this opens up" is not
                # something it can reason about -- it can only discover it by
                # moving and seeing the mask change.
                *_rank_bits(sd.launch),
                *_rank_bits(sd.target),
            ])
            out.extend(self._skill_mechanics(full[slot], actor.skill_tier))
        return out

    def _get_obs(self) -> np.ndarray:
        actor_id = self.engine.get_current_actor_id()
        # ONE call for the whole board, reused by all four hero slots and by
        # the three party-level globals below. It draws nothing -- see
        # CombatEngine.threat_forecast.
        # `death_odds=False`: the observation carries damage, stress and
        # P(targeted) and NOT `p_deaths_door`/`p_death` -- those were appended
        # here on 2026-08-25 and reverted the same day (see
        # HERO_THREAT_FORECAST). Computing them survived the revert, at 10% of
        # an env step, thrown away every time.
        forecast = ({} if self._blind_forecast
                    else self.engine.threat_forecast(death_odds=False))
        # Summed ONCE. The three party-forecast globals below took this same
        # sum twice between them, over a dict that cannot change in between --
        # dead work on the hottest path, and hoisting it is bit-exact because
        # `dict.values()` order is stable within a call and float addition is
        # only order-dependent, not call-dependent.
        forecast_damage = sum(v["damage"] for v in forecast.values())
        flat: list[float] = []
        for rank in range(RANKS):
            h = self._hero_at_rank(rank)
            flat.extend(self._hero_slot_vec(
                h, h is not None and h.id == actor_id, forecast))
        for rank in range(RANKS):
            flat.extend(self._enemy_slot_vec(self._enemy_at_rank(rank)))
        flat.extend(self._skill_slot_vecs(actor_id))
        rules = self.engine.rules
        flat.extend([
            _clamp01(self.engine.round / 15.0),
            _clamp01(self.engine.torch_level / 100.0),
            # **Divided by the PARTY, not by RANKS.** This read
            # `sum(alive) / RANKS` with RANKS hardcoded at 4, so a perfectly
            # healthy two-hero party reported 0.5 -- indistinguishable from a
            # four-hero party that had just lost half of itself, which is the
            # single most important thing this feature exists to say.
            # `engine.heroes` is appended to only at `reset()` and never
            # removed from, so its length IS the party that started this
            # combat and a dead hero still occupies a slot in it.
            #
            # `ExpeditionSequenceEnv` computes a SECOND alive fraction in its
            # context block against `_original_hero_ids`; the two are
            # deliberately different quantities -- this one is "how much of
            # the party that walked into this fight is still up", that one is
            # "how much of the expedition's roster is left" -- and they are
            # both 1.0 for an intact party of any size, which is what was
            # broken here.
            #
            # The ENEMY side keeps /RANKS: a board holds up to four enemy
            # ranks whatever arrived, and "how full is the enemy line" is the
            # question there, not "what fraction of the wave survives".
            _clamp01(sum(1 for h in self.engine.heroes if h.is_alive())
                     / max(len(self.engine.heroes), 1)),
            _clamp01(self._living_enemy_count() / RANKS),
            # How close a reinforcement wave is, and how many real actions the
            # party has already landed this round toward preventing one.
            _clamp01(self.engine.stall_counter
                     / max(rules.stall_round_summon_effect_threshold, 1)),
            _clamp01(self.engine._stall_invalidating_actions_this_round
                     / max(rules.stall_number_of_invalidating_skills_threshold, 1)),
            # Live retreat odds, not the base constant: they climb 5 points per
            # failed attempt this combat, so a policy that could not see this
            # could never learn that a second attempt is the better bet.
            _clamp01(self.engine.retreat_chance()),
            # THREAT RATIO: what the enemy board can deal in one round against
            # what the party has left to absorb it. 1.0 means the party is one
            # round from a wipe.
            #
            # This is the quantity that decides RUSH vs STALL, and without it
            # the stall timer two features up is unreadable: the policy can see
            # how close reinforcements are and still have no basis for judging
            # whether standing around is survivable. Fran's framing (2026-08-23)
            # is that enemies out-damage heroes, so stalling against four live
            # enemies is death while stalling against one is free -- and that
            # is a comparison between two numbers the observation did not carry.
            self._threat_ratio(),
            # --- APPENDED 2026-08-23 (third batch).
            # Who was caught flat-footed. 40.5% of combats open with someone
            # surprised, and it decides the whole first round.
            1.0 if self.engine.party_surprised else 0.0,
            1.0 if self.engine.monsters_surprised else 0.0,
            # A reinforcement wave arrives ONCE. After it has, stalling costs
            # nothing more, and the policy could not tell before from after.
            1.0 if self.engine._reinforcements_spawned else 0.0,
            # Consecutive misses by the acting hero -- the game's own
            # `consecutive_miss_reduction_acc_buff` makes the next attack more
            # likely to land, and that was unobservable.
            _clamp01(self.engine.consecutive_misses.get(actor_id, 0) / 3.0),
            # TURN ORDER. The single largest thing missing: the policy did not
            # know who acts next. "Kill it before it swings" and "this ally
            # goes after me so I can set up for them" are both action-economy
            # judgements that need the queue, and PLAYBOOK 3.1/3.3 are mostly
            # about exactly that.
            *self._turn_order_features(actor_id),
            # --- APPENDED 2026-08-24. The party-level forecast.
            #
            # SECURED, in Fran's sense: "most of the danger is out of the
            # question" -- not that a kill is available, but that what is
            # still coming can be healed through. 1.0 means it can. This is
            # the quantity his stall-to-recover play turns on, and the
            # observation had no way to express it.
            (0.0 if self._blind_forecast else
             _clamp01(self._heal_throughput() / max(forecast_damage, 1e-6))),
            # Incoming damage and STRESS for the whole board, against what the
            # party has to absorb each with. `_threat_ratio` two dozen
            # features up is the damage-band heuristic; these are the real
            # distributions, and the stress one has no predecessor at all.
            (0.0 if self._blind_forecast else
             _clamp01(forecast_damage
                      / max(sum(h.hp for h in self.engine.heroes
                                if h.is_alive()), 1.0))),
            (0.0 if self._blind_forecast else
             _clamp01(sum(v["stress"] for v in forecast.values()) / 40.0)),
        ])
        return np.array(flat, dtype=np.float32)

    def render(self):
        if self.render_mode == "ansi" and self.engine:
            lines = []
            # engine.log can shrink out from under a live engine (a fresh
            # CombatEngine.reset() call, e.g. ExpeditionSequenceEnv starting
            # its next combat) — treat that as "nothing shown yet" rather
            # than slicing with a now-too-large cursor (which would just
            # silently return [] and drop that combat's opening actions).
            if self._render_log_cursor > len(self.engine.log):
                self._render_log_cursor = 0
            new_log_lines = self.engine.log[self._render_log_cursor:]
            self._render_log_cursor = len(self.engine.log)
            if new_log_lines:
                lines.append("Actions:")
                lines.extend(f"  {l}" for l in new_log_lines)

            state = self.engine._get_obs()
            lines.append(f"Round {state['round']}")
            for h in state["heroes"]:
                status = "DD" if h["is_deaths_door"] else ("Dead" if h["is_dead"] else "OK")
                lines.append(f"  [P{h['position']+1}] {h['name']}: HP {h['hp']}/{h['max_hp']} Stress {h['stress']} [{status}]")
            for e in state["enemies"]:
                status = "Dead" if e["is_dead"] else f"HP {e['hp']}/{e['max_hp']}"
                lines.append(f"  [P{e['position']+1}] {e['name']}: {status}")
            return "\n".join(lines)
