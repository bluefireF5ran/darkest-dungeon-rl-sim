"""What the enemy side is ABOUT to do, one turn ahead.

Split out of `combat_engine.py` on 2026-09-08, verbatim. It is the cleanest
seam in that file: five methods, one public entry point, and exactly one
consumer -- `env/gym_env.py` folds `threat_forecast()` into the observation.

**This is the first observability change the policy demonstrably USED**
(McNemar p=0.0132; `docs/findings/rulers.md`). Every refinement measured after
it came back null, so the coarse signal is the whole of the win and there is
nothing here worth making cleverer.

It reads the board and the monster brains but writes nothing: the forecast is
a projection, and if a method here ever mutates state, that is the bug.
"""

from __future__ import annotations
from data.types import SkillLevelData
from .combatant import CombatantState
from .resolution import PROT_DAMAGE_REDUCTION_CAP, compute_hit_chance
from ._constants import DEATHBLOW_RESIST_CAP


class ForecastMixin:
    def threat_forecast(self, death_odds: bool = True) -> dict[int, dict[str, float]]:
        """What the enemy board is about to do, per hero, DRAWING NOTHING.

        `{hero_id: {"damage": float, "stress": float, "p_targeted": float}}` --
        expected HP damage and stress a hero takes over the coming round, and
        the chance anything aims at it at all.

        THIS IS THE EDGE. A human estimates it from experience; every input is
        exact here. `_enemy_skill_weights` and `_enemy_target_weights` are the
        game's own distributions -- `tools/brain_oracle.py` grades them against
        the running game at 46/46 -- and `compute_hit_chance` is validated at
        155/155 by `tools/hit_oracle.py`. `_threat_ratio`'s docstring said a
        precise answer "needs the monster brains"; this is that answer.

        Four declared simplifications, all listed so nobody reads more into the
        number than is in it:

        * a STUNNED enemy contributes nothing -- it does not act this round,
          which is the whole reason stunning buys a stall turn;
        * `extra_targets_chance` (~30 boss skills) is ignored, so a boss with
          a splash rider reads slightly low;
        * the enemy is assumed not to move, die or be moved before it acts;
        * damage is the mean of the band, so a crit reads as its expectation
          rather than as the tail that actually kills people.

        It is a FORECAST, not a guarantee, and the last point is why: the
        thing that kills a hero at 12 HP is the top of the band times the crit
        multiplier, not the mean.

        **`death_odds=False` drops `p_deaths_door`/`p_death`, and the OBSERVATION
        asks for that**, because it does not read either one. They were appended
        to the observation on 2026-08-25 and reverted the same day for being
        worth nothing measurable (`env/gym_env.py`, `HERO_THREAT_FORECAST`) --
        and computing them survived the revert. That left `_forecast_kill_chance`
        and two survival products running on the hottest function of the hottest
        path to fill two keys nobody reads: **10% of an env step, discarded.**
        Measured 2026-09-02.

        The keys are OMITTED rather than zeroed, so a caller that wants them and
        forgets to ask gets a `KeyError` instead of a plausible 0.0. Default
        `True`, so every existing caller -- `tools/time_to_secure.py`,
        `tests/tools/test_ablate_forecast.py` -- is untouched.
        """
        base = ({"damage": 0.0, "stress": 0.0, "p_targeted": 0.0,
                 "p_deaths_door": 0.0, "p_death": 0.0} if death_odds
                else {"damage": 0.0, "stress": 0.0, "p_targeted": 0.0})
        out = {h.id: dict(base) for h in self.heroes if h.is_alive()}
        # Survival products, accumulated multiplicatively and inverted at the
        # end. `_dd` is "no attack this round brings them to 0 HP"; `_hit` is
        # "no damaging attack lands at all", which is what a hero ALREADY at
        # Death's Door needs, since any landed hit rolls the deathblow.
        surv_dd = {hid: 1.0 for hid in out} if death_odds else None
        surv_hit = {hid: 1.0 for hid in out} if death_odds else None
        if not out:
            return out
        heroes_alive = [h for h in self.heroes if h.is_alive()]

        for e in self.enemies:
            if not e.is_alive() or e.is_stunned or e.is_corpse:
                continue
            ed = self.store.enemies.get(e.definition_id)
            if ed is None:
                continue
            brain = self._get_monster_brain(ed)
            usable, weights = self._enemy_skill_weights(e, ed, brain, 0)
            if not usable:
                continue
            if weights is None or not any(w > 0 for w in weights):
                weights = [1.0] * len(usable)
            total = sum(weights)
            if total <= 0:
                continue
            per_round = max(1, ed.actions_per_round)

            for (skill_id, sd), w in zip(usable, weights):
                p_skill = (w / total) * per_round
                if p_skill <= 0:
                    continue
                # Who could it hit? Mirrors _execute_enemy_skill: an AoE takes
                # every hero standing in its ranks, a single target is drawn
                # from the brain's own weighting over the non-stealthed pool.
                if sd.is_aoe:
                    positions = {p - 1 for p in sd.target}
                    hit = [(h, 1.0) for h in heroes_alive if h.position in positions]
                else:
                    pool = (heroes_alive if sd.ignore_stealth
                            else [h for h in heroes_alive if not h.is_stealthed]) or heroes_alive
                    cands, tw = self._enemy_target_weights(skill_id, brain, pool,
                                                           is_friendly=False)
                    if not cands:
                        continue
                    if tw is None or not any(x > 0 for x in tw):
                        tw = [1.0] * len(cands)
                    tt = sum(tw)
                    if tt <= 0:
                        continue
                    hit = [(c, x / tt) for c, x in zip(cands, tw) if x > 0]

                dmg_stress = self._skill_stress_damage(sd)
                is_attack = sd.heal_min is None and sd.dmg_mod > -1.0
                for target, p_target in hit:
                    slot = out.get(target.id)
                    if slot is None:
                        continue
                    p = p_skill * p_target
                    slot["p_targeted"] = min(1.0, slot["p_targeted"] + p)
                    p_hit = self._forecast_hit_chance(e, target, sd)
                    if is_attack:
                        band = (float(e.dmg_min) + float(e.dmg_max)) / 2.0
                        raw = band * (1.0 + sd.dmg_mod)
                        if not sd.ignore_protection:
                            raw *= (1.0 - min(target.prot, PROT_DAMAGE_REDUCTION_CAP))
                        slot["damage"] += p * p_hit * max(0.0, raw)
                    if dmg_stress:
                        slot["stress"] += p * p_hit * dmg_stress
                    if is_attack:
                        self._forecast_crit_stress(out, e, target, sd,
                                                   p * p_hit, heroes_alive)
                        if death_odds:
                            land = p * p_hit
                            surv_hit[target.id] *= (1.0 - min(1.0, land))
                            kill = self._forecast_kill_chance(e, target, sd)
                            surv_dd[target.id] *= (1.0 - min(1.0, land * kill))

        # A hero brought to 0 HP ENTERS Death's Door; it does not kill them.
        # Only a hero already there dies, and then on any landed damaging hit
        # at `deathblow_chance` minus their (capped) DEATHBLOW RES.
        #
        # A hero who enters Death's Door and is then hit AGAIN in the same
        # round can die, and that second-order path is deliberately NOT
        # modelled: it needs the order of the enemy queue, which the forecast
        # does not have. So `p_death` reads 0 for a healthy hero -- correct to
        # first order, and the reason `p_deaths_door` is a separate feature
        # rather than being folded in.
        if not death_odds:
            return out
        for h in self.heroes:
            if not h.is_alive() or h.id not in out:
                continue
            slot = out[h.id]
            if h.is_deaths_door:
                resist = min(h.death_blow_resist, DEATHBLOW_RESIST_CAP)
                chance = max(0.0, float(self.rules.deathblow_chance) - resist)
                slot["p_death"] = (1.0 - surv_hit[h.id]) * chance
            else:
                slot["p_deaths_door"] = 1.0 - surv_dd[h.id]
        return out

    def _forecast_kill_chance(self, attacker: CombatantState,
                              target: CombatantState,
                              sd: SkillLevelData) -> float:
        """P(this attack alone brings `target` to 0 HP), GIVEN that it lands.

        The forecast's mean is useless for this and that is the whole point:
        `tools/time_to_secure.py` showed two lethality criteria reading 100%
        safe at round 0 on every tier, because an AVERAGE round never kills
        anybody. The stated objective is "without anyone dying", which is a
        property of the TAIL, so the tail has to be computed.

        Damage is `Uniform[dmg_min, dmg_max] x (1 + dmg_mod)`, reduced by PROT
        unless the skill pierces it, and multiplied by the monster crit
        multiplier on a crit. So the kill chance is the uniform's upper tail
        past `hp / scale`, mixed over crit and non-crit.

        Rounding (`max(1, round(applied))`) is ignored: it moves the threshold
        by less than half a point of HP.
        """
        lo, hi = float(attacker.dmg_min), float(attacker.dmg_max)
        if hi <= 0.0:
            return 0.0
        scale = 1.0 + sd.dmg_mod
        if not sd.ignore_protection:
            scale *= (1.0 - min(target.prot, PROT_DAMAGE_REDUCTION_CAP))
        if scale <= 0.0:
            return 0.0

        def tail(mult: float) -> float:
            """P(Uniform[lo, hi] * scale * mult >= target.hp)."""
            k = scale * mult
            if k <= 0.0:
                return 0.0
            need = float(target.hp) / k
            if need <= lo:
                return 1.0
            if need >= hi:
                return 0.0
            return (hi - need) / (hi - lo) if hi > lo else 0.0

        crit = max(0.0, min(1.0, float(attacker.crit) + float(sd.crit)
                            + float(target.crit_received_bonus)))
        return ((1.0 - crit) * tail(1.0)
                + crit * tail(float(self.rules.monster_crit_multiplier)))

    def _forecast_crit_stress(self, out: dict, attacker: CombatantState,
                              target: CombatantState, sd: SkillLevelData,
                              p_landed: float,
                              heroes_alive: list[CombatantState]) -> None:
        """Expected stress from a MONSTER CRIT, spread over the whole party.

        Measured 2026-08-25 and this is why it is here: with only the skills'
        own `.effect` stress, the forecast UNDER-predicted realised stress by
        about 2x at Veteran and Champion (ratios 1.97 and 2.11 over ~450
        rounds a tier). Under-prediction is the dangerous direction -- the
        feature exists so a hero can be seen to be in trouble.

        A monster crit charges `monster_crit_stress_damage` (10) to whoever it
        hit and `monster_crit_companion_stress_chance` x
        `monster_crit_companion_stress_damage` (0.5 x 5) to every OTHER living
        hero, so one crit is worth 17.5 party stress with four alive. That is
        the single largest stress source in a normal round and none of it was
        being predicted.

        NOTE THAT IT IS NON-LOCAL: an attack aimed at the front rank raises
        the forecast stress of the hero at the BACK. That is faithful, and it
        is most of what makes a crit frightening.
        """
        crit = (float(attacker.crit) + float(sd.crit)
                + float(target.crit_received_bonus))
        crit = max(0.0, min(1.0, crit))
        if crit <= 0.0 or p_landed <= 0.0:
            return
        p_crit = p_landed * crit
        slot = out.get(target.id)
        if slot is not None:
            slot["stress"] += p_crit * float(self.rules.monster_crit_stress_damage)
        companion = (float(self.rules.monster_crit_companion_stress_chance)
                     * float(self.rules.monster_crit_companion_stress_damage))
        if companion <= 0.0:
            return
        for ally in heroes_alive:
            if ally.id == target.id:
                continue
            a_slot = out.get(ally.id)
            if a_slot is not None:
                a_slot["stress"] += p_crit * companion

    def _forecast_hit_chance(self, attacker: CombatantState,
                             target: CombatantState, sd: SkillLevelData) -> float:
        """P(hit) under the game's own roll cutoffs, for the forecast only.

        `compute_hit_chance` returns the raw unclamped chance and
        `resolve_to_hit` brackets the ROLL, so the probability a roll lands is
        `cutoffAlwaysMiss + min(1 - cutoffAlwaysMiss, raw)` -- 0.05 + min(0.95,
        raw) on the shipped constants. Do not substitute a clamp on the chance:
        that is the exact 5pp error corrected on 2026-08-19 (DECOMP.md F4).
        """
        raw = compute_hit_chance(attacker, target, sd, self.rules)
        lo = float(self.rules.cutoff_always_miss)
        hi = float(self.rules.cutoff_always_hit)
        return max(0.0, min(1.0, lo + min(hi - lo, max(0.0, raw))))

    def _skill_stress_damage(self, sd: SkillLevelData) -> float:
        """Stress this skill inflicts if it lands, summed over its effects.

        Stress is the currency the stated objective is denominated in, and it
        was invisible at every level: the observation carried neither what a
        hero was about to take nor what the board could deal.

        `.effect` names are resolved through `store.effects` the way every
        other call site does, and read via `_named_effects` because the parser
        returns `.effect ""` as `['']` and a single effect as a bare `str`.
        """
        total = 0.0
        for ename in self._named_effects(sd.effects):
            eff = self.store.effects.get(ename)
            if eff is None:
                continue
            amount = getattr(eff, "stress_damage", None)
            if amount:
                chance = getattr(eff, "chance", None)
                total += float(amount) * (1.0 if chance is None else min(1.0, float(chance)))
        return total
