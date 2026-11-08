"""How the enemy side chooses a skill, chooses a target, and takes its turn.

Split out of `combat_engine.py` on 2026-09-08, verbatim.

Driven by the game's own `base.monster_brains.json` weights rather than by
anything invented here, and validated against the real game:
`tools/brain_oracle.py` grades `_select_enemy_skill` against the weight
vectors the release build prints over its debug channel, and 46 of 46
gradeable contributions match.

**A monster-brain gate belongs to the DESIRE, not to its type.** Two desires
of the same type can carry different gates, so the gate is evaluated per
desire -- `_skill_desire_gates_pass` -- and not once per type. Getting that
backwards is a bug the data hides, because most types carry one desire.

**A gated desire is reported by the oracle but never scored.** The captured
trace has no board state in it, so a zero contribution is equally consistent
with a shut gate; the corpus can fail to contradict the gate work, never
confirm it.

Target weights carry a `1e-4` epsilon per desire and skill weights carry
none. Recorded rather than implemented, because it only decides anything when
every weight is zero, and `weighted_choice` already goes uniform there.
"""

from __future__ import annotations
from data.types import EnemyDefinition, SkillLevelData
from .combatant import CombatantState
from .resolution import resolve_attack


class MonsterAIMixin:
    def _get_monster_brain(self, ed: EnemyDefinition):
        return self.store.monster_brains.get(ed.monster_brain_id) or self.store.monster_brains.get("default")

    def _base_class_id(self, definition_id: str) -> str:
        """Strip a trailing _<single-letter> variant suffix (e.g.
        swine_piglet_A/_B/_C -> swine_piglet, brigand_barrel_D ->
        brigand_barrel) — the real base-class matching convention used by
        ally_base_class_id (verified: swine_prince's ally_dead_skill desire
        references 'swine_piglet', matching all 3 swine_piglet_A/B/C
        variants; hero ids like 'jester' have no such suffix and pass
        through unchanged)."""
        parts = definition_id.rsplit("_", 1)
        if len(parts) == 2 and len(parts[1]) == 1 and parts[1].isalpha():
            return parts[0]
        return definition_id

    def _select_enemy_skill(self, enemy: CombatantState, ed: EnemyDefinition, brain, action_idx: int = 0):
        """Draw one skill from `_enemy_skill_weights`. See that method: the
        weighting is separated from the draw so a caller can READ the
        distribution without disturbing the RNG stream."""
        usable, weights = self._enemy_skill_weights(enemy, ed, brain, action_idx)
        if usable is None:
            return None, None
        if weights is None or not any(w > 0 for w in weights):
            return self.rng.choice(usable)
        return self.rng.weighted_choice(usable, weights)

    def _enemy_skill_weights(self, enemy: CombatantState, ed: EnemyDefinition, brain,
                             action_idx: int = 0):
        """`(usable, weights)` for this enemy's skill choice -- everything
        `_select_enemy_skill` knows, one step before it throws the dice away.
        `usable` is None when the enemy has no usable skill at all, and
        `weights` is None when nothing weighted applies (a uniform draw).

        DRAWS NOTHING, and that is the whole point. `tools/brain_oracle.py`
        grades these weights against the running game at 46/46, so they are
        the best available prediction of what the enemy does next -- and a
        reader must be able to take it without moving the RNG. A redundant
        draw would invalidate every trace comparison after it, the same
        discipline `.dot_irresistible` follows by SKIPPING its roll rather
        than rolling against a zero resist.

        Weighted pick among the enemy's skills using its brain's skill
        selection desires. Falls back to uniform random among usable skills
        if there's no brain or no applicable desire — the old always-random
        behavior, not a regression. Filters out skills blocked by
        per-battle/per-turn limits, invalid mode, or cooldown first.

        Every real desire type is modeled except fill_ally_captor_empty_skill
        (The Hag's capture mechanic, out of scope — see ASSUMPTIONS.md).
        preferred_skill is folded into the same flat-spread bucket as
        random_skill (verified data-equivalent — see SkillDesire docstring).
        """
        usable = []
        for sk in ed.skills:
            if not sk.levels:
                continue
            sd = sk.levels[max(sk.levels.keys())]
            # `.launch` is the rank the monster must STAND IN to use the
            # skill. It was parsed but never read on the enemy side until
            # 2026-09-19: a rank-1 Bone Courtier fired `tempting_goblet`
            # (`.launch 432`) in 106 of 200 draws. 228 of 694 enemy skills
            # are launch-restricted and 23 of those are stress attacks, so
            # shuffling a stress caster out of its firing rank -- the whole
            # point of disruption -- was a no-op, while `engine/action.py`
            # enforced the same field for heroes all along. A size>1 monster
            # passes if ANY occupied rank qualifies, mirroring how `.target`
            # resolves against it in `_execute_enemy_skill` below.
            if sd.launch and {p - 1 for p in sd.launch}.isdisjoint(
                    enemy.occupied_positions):
                continue
            if not self._check_skill_limits(enemy, sk.id, sd):
                continue
            if not self._check_valid_mode(enemy, sd):
                continue
            if enemy.skill_cooldowns.get(sk.id, 0) > 0:
                continue
            usable.append((sk.id, sd))
        if not usable:
            return None, None

        weights = None
        if brain and brain.skill_desires:
            specific_weight = {sk_id: 0.0 for sk_id, _ in usable}
            flat_weight_total = 0.0
            heroes_alive = [h for h in self.heroes if h.is_alive()]
            living_enemies = [e for e in self.enemies if e.is_alive()]

            def add_weight(chance: float, combat_skill_id: str):
                nonlocal flat_weight_total
                if combat_skill_id and combat_skill_id in specific_weight:
                    specific_weight[combat_skill_id] += chance
                else:
                    flat_weight_total += chance

            for d in brain.skill_desires:
                if not self._skill_desire_gates_pass(d, enemy, action_idx,
                                                     heroes_alive, living_enemies):
                    continue
                if d.type == "specific_skill":
                    if d.combat_skill_id in specific_weight:
                        specific_weight[d.combat_skill_id] += d.base_chance
                elif d.type in ("random_skill", "preferred_skill"):
                    flat_weight_total += d.base_chance
                elif d.type == "performing_turn_skill":
                    if d.performing_turn is not None and action_idx + 1 == d.performing_turn:
                        add_weight(d.base_chance, d.combat_skill_id)
                elif d.type == "stun_skill":
                    add_weight(d.base_chance, d.combat_skill_id)
                elif d.type in ("ally_alive_skill", "ally_dead_skill"):
                    if not d.ally_base_class_id:
                        continue
                    matches = [e for e in self.enemies if e is not enemy
                               and self._base_class_id(e.definition_id) == d.ally_base_class_id]
                    condition = (any(e.is_alive() for e in matches) if d.type == "ally_alive_skill"
                                 else any(e.is_dead for e in matches))
                    if condition:
                        add_weight(d.base_chance, d.combat_skill_id)
                elif d.type == "heal_skill":
                    # The shared gate covers `hp_ratio_threshold` when the key
                    # is present; a heal desire WITHOUT one still only fires
                    # if somebody is actually hurt, which is the same test at
                    # a threshold of 1.0.
                    if d.hp_ratio_threshold is None and not any(
                            e.hp < e.max_hp for e in living_enemies):
                        continue
                    if d.combat_skill_id:
                        add_weight(d.base_chance, d.combat_skill_id)
                    else:
                        heal_skill_ids = [sk_id for sk_id, sdd in usable if sdd.heal_min is not None]
                        if heal_skill_ids:
                            per = d.base_chance / len(heal_skill_ids)
                            for hid in heal_skill_ids:
                                specific_weight[hid] += per
                elif d.type == "effect_key_status_skill":
                    condition = any(h.is_marked for h in heroes_alive) if d.effect_key_status == "tagged" else False
                    if condition:
                        add_weight(d.base_chance, d.combat_skill_id)

            per_skill_flat = flat_weight_total / len(usable)
            weights = [specific_weight[sk_id] + per_skill_flat for sk_id, _ in usable]

        return usable, weights

    def _skill_desire_gates_pass(self, d, enemy: CombatantState, action_idx: int,
                                 heroes_alive: list[CombatantState],
                                 living_enemies: list[CombatantState]) -> bool:
        """Every gate a skill_selection_desire can carry, applied to EVERY
        desire whatever its type.

        These used to live inside the branch of whichever desire type they
        were first noticed on -- `non_stunned_heroes_min` in `stun_skill`,
        `monsters_size_*`/`hp_ratio_threshold`/`first_initiative_only` in
        `heal_skill`. The shipped data puts the same keys on `specific_skill`
        (20 distinct kinds, 130+ instances) and `performing_turn_skill` (44),
        where nothing read them at all: **21.8% of the hall/room monster pool
        carries at least one gate the engine ignored.** A gate is a property
        of the desire, not of its type.

        The two with real reach are `monsters_size_*` (11.4% of pool slots --
        the "is there room on the board" gate every summon carries) and
        `marked_heroes_max` (9.0% -- what stops a Cultist Witch re-marking an
        already-marked party).
        """
        # A False here means the skill belongs to the game's no-corpse
        # ruleset. Corpses are always on in this simulator, so the desire can
        # never apply (Skeleton Bearer's unholy_rally, the Hateful Virago's
        # from_death_comes_life).
        if not d.is_valid_when_corpse_enabled:
            return False
        if d.first_initiative_only and not self._first_enemy_this_round(enemy):
            return False
        if d.performing_turn_min is not None and action_idx + 1 < d.performing_turn_min:
            return False

        # ---- the monster side ----
        if d.monsters_min is not None and len(living_enemies) < d.monsters_min:
            return False
        if d.monsters_max is not None and len(living_enemies) > d.monsters_max:
            return False
        if d.monsters_size_min is not None or d.monsters_size_max is not None:
            # DECLARED READING: total rank occupancy, not a head count. The
            # Siren's conch_summon carries `monsters_min/max` AND
            # `monsters_size_min/max` on the same desire, so the two keys
            # cannot denote one quantity, and only one of them is named for
            # size. See ASSUMPTIONS.md for the falsifier.
            occupancy = sum(e.size for e in living_enemies)
            if d.monsters_size_min is not None and occupancy < d.monsters_size_min:
                return False
            if d.monsters_size_max is not None and occupancy > d.monsters_size_max:
                return False
        if d.guarded_monsters_min is not None or d.guarded_monsters_max is not None:
            guarded = sum(1 for e in living_enemies if e.guard_source_id is not None)
            if d.guarded_monsters_min is not None and guarded < d.guarded_monsters_min:
                return False
            if d.guarded_monsters_max is not None and guarded > d.guarded_monsters_max:
                return False
        if d.hp_ratio_threshold is not None and not any(
                (e.hp / max(e.max_hp, 1)) < d.hp_ratio_threshold for e in living_enemies):
            return False

        # ---- the hero side ----
        if d.heroes_min is not None and len(heroes_alive) < d.heroes_min:
            return False
        if d.non_stunned_heroes_min is not None and sum(
                1 for h in heroes_alive if not h.is_stunned) < d.non_stunned_heroes_min:
            return False
        if d.non_deaths_door_heroes_min is not None and sum(
                1 for h in heroes_alive if not h.is_deaths_door) < d.non_deaths_door_heroes_min:
            return False
        if (d.marked_heroes_min is not None or d.marked_heroes_max is not None
                or d.non_marked_heroes_min is not None):
            marked = sum(1 for h in heroes_alive if h.is_marked)
            if d.marked_heroes_min is not None and marked < d.marked_heroes_min:
                return False
            if d.marked_heroes_max is not None and marked > d.marked_heroes_max:
                return False
            if (d.non_marked_heroes_min is not None
                    and len(heroes_alive) - marked < d.non_marked_heroes_min):
                return False
        if d.virtued_heroes_max is not None and sum(
                1 for h in heroes_alive if h.is_virtuous) > d.virtued_heroes_max:
            return False
        return True

    def _first_enemy_this_round(self, enemy: CombatantState) -> bool:
        """True if `enemy` is the first (fastest-acting) enemy to take a
        turn this round — backs heal_skill's first_initiative_only gate
        (e.g. cyst's beholder_healself: only bother self-healing check once
        per round, not once per monster). Reset each round in _end_of_round."""
        if self._first_enemy_acted_this_round:
            return False
        return True

    def _select_enemy_target(self, skill_id: str, brain, candidates: list[CombatantState],
                               is_friendly: bool = False) -> CombatantState | None:
        """Draw one target from `_enemy_target_weights`. See that method."""
        cands, weights = self._enemy_target_weights(skill_id, brain, candidates,
                                                    is_friendly)
        if cands is None:
            return None
        if weights is None:
            return cands[self.rng.randint(0, len(cands) - 1)]
        return self.rng.weighted_choice(cands, weights)

    def _enemy_target_weights(self, skill_id: str, brain,
                              candidates: list[CombatantState],
                              is_friendly: bool = False):
        """`(candidates, weights)` for this skill's target choice, DRAWING
        NOTHING -- the target-side twin of `_enemy_skill_weights`. `weights`
        is None when no desire applies, i.e. a uniform draw.

        Weighted pick among candidates (heroes, or — when is_friendly —
        the performer's own side) using target_selection_desires that apply
        to this skill (empty specific_combat_skill_id = applies regardless
        of skill) and match the friendly/enemy side of this pool. Falls back
        to uniform random if there's no brain or no applicable desire.

        rank_target is folded into the same weighting as random_target
        (verified data-equivalent — see TargetDesire docstring).

        Which desires apply comes from BOTH side flags, not from
        `is_friendly_target_desire` alone: 25 desires set both, and every one
        of them is the brain's baseline `random_target`. Reading one flag
        dropped that baseline from enemy targeting for 9 monsters in the
        hall/room pool (Swinetaur, Octotank, Fishman Shaman), which promoted
        every conditional desire they own — "prefer a marked hero" became
        "hit the marked hero, always".
        """
        if not candidates:
            return None, None
        applicable = [d for d in (brain.target_desires if brain else [])
                      if d.specific_combat_skill_id in ("", skill_id)
                      and (d.is_friendly_target_desire if is_friendly
                           else d.is_enemy_target_desire)]
        # An exclusive desire suppresses the ordinary ones outright — the
        # Fishman Shaman's heal picks the most wounded ally rather than
        # competing against a flat random weight.
        exclusive = [d for d in applicable if d.is_exclusive_desire]
        if exclusive:
            applicable = exclusive
        if not applicable:
            return candidates, None

        side = self.heroes if (candidates and candidates[0].is_hero) else self.enemies
        last_one_standing = sum(1 for c in side if c.is_alive()) <= 1

        weights = []
        for target in candidates:
            w = 0.0
            for d in applicable:
                # Per-desire exclusions. These do not veto the target, only
                # this desire's opinion of it: the flat random desire still
                # keeps an excluded hero reachable, which is what makes
                # "never aim the stun at someone already stunned" a
                # preference rather than an immunity.
                if not d.can_target_stunned and target.is_stunned:
                    continue
                if not d.can_target_deaths_door and target.is_deaths_door:
                    continue
                if not d.can_target_last_hero and last_one_standing:
                    continue
                if not d.can_target_afflicted and target.is_afflicted:
                    continue
                if not d.can_target_virtued and target.is_virtuous:
                    continue
                if (not d.can_target_not_overstressed
                        and not (target.is_afflicted or target.is_virtuous)):
                    # Reads backwards on purpose: the flag excludes the hero
                    # who is NOT overstressed, so a desire carrying it aims at
                    # heroes who have already broken.
                    continue
                if not d.is_guarded and target.guard_source_id is not None:
                    continue
                if not d.is_guarding and target.guard_target_id is not None:
                    continue
                if d.type in ("random_target", "rank_target"):
                    w += d.base_chance
                elif d.type == "marked_target" and target.is_marked:
                    w += d.base_chance
                elif d.type == "stunned_target" and target.is_stunned:
                    w += d.base_chance
                elif d.type == "health_target":
                    hp_frac = target.hp / max(target.max_hp, 1)
                    w += d.base_chance * (hp_frac if d.is_greater_comparison else (1.0 - hp_frac))
                elif d.type == "stress_target":
                    stress_frac = target.stress / 100.0
                    w += d.base_chance * (stress_frac if d.is_greater_comparison else (1.0 - stress_frac))
                elif d.type == "resistance_target":
                    resist = self._get_resistance(target, d.resistance_type_id)
                    w += d.base_chance * (resist if d.is_greater_comparison else (1.0 - resist))
                elif d.type == "ally_class_target":
                    if d.ally_base_class_id and self._base_class_id(target.definition_id) == d.ally_base_class_id:
                        w += d.base_chance
            weights.append(w)
        return candidates, weights

    def _finish_enemy_action(self, enemy: CombatantState, skill_id: str, sd: SkillLevelData, brain):
        # `.move` applies on EVERY enemy action path -- this hook is the one
        # place all three branches (self-target, friendly, enemy) meet.
        # Hit or miss, matching the hero path, where `_apply_movement` sits
        # outside the `if result.hit` guard: the Necromancer retreats whether
        # or not the smite lands.
        #
        # Called ONCE per action rather than once per target. The hero path
        # calls it inside its target loop, which would walk an AoE user one
        # rank per target; that is a separate pre-existing bug, not copied.
        if sd.move_self:
            self._apply_movement(enemy, enemy, sd)
        self._track_skill_use(enemy, skill_id, sd)
        if brain and skill_id in brain.skill_cooldowns:
            enemy.skill_cooldowns[skill_id] = brain.skill_cooldowns[skill_id]

    def _execute_enemy_turn(self, enemy: CombatantState):
        ed = self._get_enemy_def(enemy.id)
        if not ed or not ed.skills:
            return
        brain = self._get_monster_brain(ed)

        for action_idx in range(ed.actions_per_round):
            skill_id, sd = self._select_enemy_skill(enemy, ed, brain, action_idx)
            if sd is None:
                break
            self._execute_enemy_skill(enemy, skill_id, sd, brain)

        self._first_enemy_acted_this_round = True

    def _execute_enemy_skill(self, enemy: CombatantState, skill_id: str,
                               sd: SkillLevelData, brain) -> None:
        """Resolve one already-selected enemy skill use — shared by the
        normal per-round turn loop (_execute_enemy_turn) and bonus-initiative
        actions (_resolve_bonus_initiative), which pick a skill outside the
        normal per-action selection loop (often via combat_skill_id_override)
        but need the exact same self/friendly/enemy-targeting execution."""
        if sd.atk_dmg_max == 0 and sd.atk_dmg_min == 0:
            # A `.dmg 0 0` skill (stress attacks, marks, curses) deals no
            # damage -- `resolve_attack` reads a zero band as that. The band
            # is put back afterwards because it is also what the observation
            # and the threat forecast show as this monster's attack, and that
            # meaning predates this fix (2026-10-06).
            band = (enemy.dmg_min, enemy.dmg_max)
            enemy.dmg_min = enemy.dmg_max = 0
            try:
                self._execute_enemy_skill_with_band(enemy, skill_id, sd, brain)
            finally:
                enemy.dmg_min, enemy.dmg_max = band
            return
        self._execute_enemy_skill_with_band(enemy, skill_id, sd, brain)

    def _execute_enemy_skill_with_band(self, enemy: CombatantState, skill_id: str,
                                       sd: SkillLevelData, brain) -> None:
        self._sync_all_conditional_buffs(performer=enemy, skill_id=skill_id)
        if sd.atk_dmg_min:
            enemy.dmg_min = max(1, round(sd.atk_dmg_min * self._monster_dmg_mult))
        if sd.atk_dmg_max:
            enemy.dmg_max = max(1, round(sd.atk_dmg_max * self._monster_dmg_mult))

        is_heal = sd.heal_min is not None and sd.heal_max is not None

        if not sd.target and not sd.is_aoe:
            # Self-only skill (no .target positions at all — e.g. cyst's
            # beholder_healself), same "empty .target" convention hero
            # self-actions use.
            if is_heal:
                applied = self._apply_heal(enemy, self.rng.randint(sd.heal_min, sd.heal_max), enemy)
                self.log.append(f"{enemy.name} heals self for {applied}")
            self._apply_skill_effects(enemy, enemy, sd, hit=True)
            self._finish_enemy_action(enemy, skill_id, sd, brain)
            return

        if sd.targets_allies:
            # Friendly-targeting monster skill (verified real: Collector
            # Shaman's head_heal/head_buff, Cultist Orgiastic's
            # orgiastic_heal/orgiastic_guard, Totem Guard's
            # totem_bolster, Fuseman's light_fuse — all real ".target
            # @..." monster skills that heal/buff an ally enemy instead
            # of attacking a hero). No attack roll: heals apply directly
            # like the hero heal path, buffs/effects apply unconditionally
            # (verified their EffectDefinitions carry on_hit=on_miss=True,
            # i.e. they're meant to always land, not gate on a hit roll).
            pool = [e for e in self.enemies if e.is_alive()]
            if not pool:
                return
            if sd.is_aoe:
                positions = {p - 1 for p in sd.target}
                targets = [e for e in pool
                           if not positions.isdisjoint(e.occupied_positions)]
                if not targets:
                    return
            else:
                target = self._select_enemy_target(skill_id, brain, pool, is_friendly=True)
                if target is None:
                    return
                targets = [target]

            for target in targets:
                if is_heal:
                    applied = self._apply_heal(target, self.rng.randint(sd.heal_min, sd.heal_max), enemy)
                    self.log.append(f"{enemy.name} heals {target.name} for {applied}")
                else:
                    self._apply_skill_effects(target, enemy, sd, hit=True)
            self._finish_enemy_action(enemy, skill_id, sd, brain)
            return

        heroes_alive = [h for h in self.heroes if h.is_alive()]
        if not heroes_alive:
            return

        if sd.is_aoe:
            # AoE hits everyone occupying its target ranks, stealthed or
            # not — stealth protects against being singled out by a
            # single-target skill, not against an attack that covers the
            # whole formation. (Previously declared as an unmodeled gap;
            # now that AoE actually multi-targets, this is the correct
            # behavior rather than a simplification.)
            positions = {p - 1 for p in sd.target}
            targets = [h for h in heroes_alive if h.position in positions]
            if not targets:
                return
        else:
            # Stealthed heroes are excluded from single-target selection
            # (can't pick them out of the crowd) UNLESS this specific skill
            # ignores stealth (real flag — e.g. Crow's distract, Cyst's
            # beholder_gaze/burn, Prophet's prophet_mark/prophet_attack/
            # eye_on_you, Brigand Sapper's sapper_detonate — wiki-verified
            # via the same flag's hero-skill equivalent, Highwayman's
            # "Tracking Shot": "Bypass/Remove Stealth"); if everyone is
            # stealthed, fall back to the full pool so the enemy can
            # still act.
            targetable = heroes_alive if sd.ignore_stealth else [h for h in heroes_alive if not h.is_stealthed]
            target_pool = targetable if targetable else heroes_alive
            target = self._select_enemy_target(skill_id, brain, target_pool, is_friendly=False)
            if target is None:
                return
            targets = [target]

            # Extra targets — a real chance-based mechanic on ~30 boss/named
            # monster skills (see SkillLevelData.extra_targets_chance/count).
            # Each of extra_targets_count (always 1 in real data) rolls
            # extra_targets_chance independently; on success, one more
            # DISTINCT target from the same eligible pool is added and
            # receives the identical attack/effect resolution below.
            if sd.extra_targets_count and sd.extra_targets_chance:
                remaining_pool = [h for h in target_pool if h is not target]
                for _ in range(sd.extra_targets_count):
                    if not remaining_pool or self.rng.random() >= sd.extra_targets_chance:
                        continue
                    extra_target = self._select_enemy_target(skill_id, brain, remaining_pool, is_friendly=False)
                    if extra_target is None:
                        continue
                    targets.append(extra_target)
                    remaining_pool = [h for h in remaining_pool if h is not extra_target]

        shared_hit_roll = self._shared_hit_roll(targets)
        for target in targets:
            if not sd.ignore_guard:
                target = self._resolve_guard_redirect(target, is_aoe=sd.is_aoe)
            acc_bonus = (self.consecutive_misses.get(enemy.id, 0) *
                         self.rules.consecutive_miss_reduction_acc_buff)
            result = resolve_attack(enemy, target, sd, self.rules, self.rng,
                                      acc_bonus=acc_bonus, hit_roll=shared_hit_roll)

            if result.hit:
                self.log.append(f"{enemy.name} hits {target.name} for {result.applied_damage}" +
                              (" (crit!)" if result.is_crit else ""))
                self.consecutive_misses[enemy.id] = 0
                if result.is_crit:
                    self._apply_stress(target, self.rules.monster_crit_stress_damage)
                    self._apply_stress_to_allies(
                        target, self.rules.monster_crit_companion_stress_chance,
                        self.rules.monster_crit_companion_stress_damage, exclude=target,
                    )
                if result.target_deaths_door:
                    self._handle_deaths_door(target, enemy, is_crit=result.is_crit)
            else:
                self.log.append(f"{enemy.name} misses {target.name}")
                self.consecutive_misses[enemy.id] = self.consecutive_misses.get(enemy.id, 0) + 1

            if sd.can_be_riposted:
                self._maybe_trigger_riposte(target, enemy, result.hit)
            # Monster skill effects (bleed, mark, debuffs, ...) were never
            # applied before — _execute_enemy_turn only ever resolved raw
            # damage. Real monster skill defs do carry populated .effects
            # lists (e.g. cultist_brawler_A's brawler_claw: Minor Bleed 1,
            # SmallDmg Marked Target, Brawler Rend Debuff 1) — wire them in
            # the same way hero skills already do.
            self._apply_skill_effects(target, enemy, sd, result.hit, is_crit=result.is_crit)

        self._finish_enemy_action(enemy, skill_id, sd, brain)

    def _resolve_bonus_initiative(self, is_round_start: bool):
        """Scripted bonus actions for boss-tier brains — see
        BonusInitiativeDesire's docstring. Called once at the very start of
        a round (before normal turn order) and once at the very end
        (_end_of_round, before the round counter advances)."""
        for enemy in list(self.enemies):
            if enemy.is_corpse:
                continue
            ed = self._get_enemy_def(enemy.id)
            if not ed:
                continue
            brain = self._get_monster_brain(ed)
            if not brain or not brain.bonus_initiative_desires:
                continue
            living_enemies = [e for e in self.enemies if e.is_alive()]
            heroes_alive = [h for h in self.heroes if h.is_alive()]

            for d in brain.bonus_initiative_desires:
                timing_matches = ((is_round_start and d.is_round_start and d.is_pre_turn) or
                                   (not is_round_start and d.is_round_finish and d.is_post_turn))
                if not timing_matches:
                    continue
                if not self._check_bonus_initiative_condition(enemy, d, living_enemies, heroes_alive):
                    continue

                skill_id = d.combat_skill_id_override
                sd = None
                if skill_id:
                    sk = next((s for s in ed.skills if s.id == skill_id), None)
                    if sk and sk.levels:
                        sd = sk.levels[max(sk.levels.keys())]
                if sd is None:
                    skill_id, sd = self._select_enemy_skill(enemy, ed, brain, 0)
                if sd is None:
                    continue
                self.log.append(f"{enemy.name} takes a bonus action ({d.type})")
                self._execute_enemy_skill(enemy, skill_id, sd, brain)

    def _check_bonus_initiative_condition(self, enemy: CombatantState, d, living_enemies, heroes_alive) -> bool:
        if d.monsters_min is not None and len(living_enemies) < d.monsters_min:
            return False
        if d.monsters_max is not None and len(living_enemies) > d.monsters_max:
            return False
        if d.monsters_size_limit is not None and len(living_enemies) > d.monsters_size_limit:
            return False

        if d.type == "guaranteed":
            return True
        elif d.type == "ally_actor_class_count":
            if not d.ally_base_class_id:
                return False
            count = sum(1 for e in living_enemies
                        if self._base_class_id(e.definition_id) == d.ally_base_class_id)
            if d.ally_count_min is not None and count < d.ally_count_min:
                return False
            if d.ally_count_max is not None and count > d.ally_count_max:
                return False
            return True
        elif d.type == "hp_ratio_threshold":
            if d.heroes_min is not None and len(heroes_alive) < d.heroes_min:
                return False
            if d.heroes_max is not None and len(heroes_alive) > d.heroes_max:
                return False
            if d.health_ratio_threshold is None:
                return False
            hp_frac = enemy.hp / max(enemy.max_hp, 1)
            return (hp_frac < d.health_ratio_threshold) if d.is_under_threshold else (hp_frac >= d.health_ratio_threshold)
        elif d.type == "last_skill":
            return bool(d.last_combat_skill_id) and self._last_skill_used == d.last_combat_skill_id
        return False
