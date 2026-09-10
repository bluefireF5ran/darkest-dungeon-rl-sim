"""Stress, afflictions, virtues, and the twelve ways a broken hero acts out.

Split out of `combat_engine.py` on 2026-09-08, verbatim.

**`_ACT_OUT_HANDLERS` is why this block could only ever move as one piece.**
It maps twelve strings to the twelve `_act_out_*` functions as BARE function
objects, resolved in the class body's own namespace at class-creation time --
not `getattr(self, name)` at call time. Split the dict from its handlers, or
even reorder them above it, and the class stops being constructible. It is
consumed by `_maybe_act_out` through `self._ACT_OUT_HANDLERS.get(row.kind)`,
which resolves through the MRO like any other attribute, so a mixin is fine
where a scatter would not be.

Stress is the defining hazard of a long expedition, and this is the machinery
that decides what a hero at 100 does about it: the affliction/virtue draw
(`_draw_trait`, a 25% virtue lottery), the per-turn act-out roll, and the
mode revert that pulls a stance hero back out of its transformed form.

**Act-outs only fire in special cases** -- an AFFLICTED hero, and in the real
game a Crimson Curse one too, which this simulator declares out of scope
(Fran, 2026-09-08). So affliction is the only route in here, and it reaches
roughly 0.6% of env steps in normal play.

That makes this the hardest subsystem in the engine to exercise by accident,
and it is a warning about how to verify a change to it: a random-action walk
like `tools/trajectory_oracle.py` will play thousands of episodes without
entering this code at all, so a byte-identical trajectory report says nothing
about these twelve functions. When this block moved, the handler table was
checked directly -- twelve entries, each still the same function object the
class exposes -- because the oracle could not have noticed if it were not.

`-startafflicted` and `-startvirtued` on the real game are how the subsystem
gets validated against ground truth at all; see `tools/CLAUDE.md`.
"""

from __future__ import annotations
from data.types import TraitActOut, TraitDefinition
from typing import Optional
from .combatant import ActiveBuff, CombatantState
from .stress import apply_stress
from ._constants import (
    HEART_ATTACK_STRESS_THRESHOLD, VIRTUE_SELF_HEAL_MAX_HP_FRACTION,
    _int_stat_snapshot)


class AfflictionMixin:
    def _apply_stress(self, target: CombatantState, amount: float, chance: float = 1.0):
        if not target.is_hero:
            return
        chance = min(1.0, chance + self._stress_chance_bonus)
        if self.rng.random() < chance:
            effective_mult = self._stress_damage_mult * max(0.0, target.stress_dmg_received_mult)
            # Only the TRANSITION into afflicted/virtuous should apply the
            # resolve-check consequences. This used to be an unconditional
            # "is the hero afflicted?" check after the fact, so every later
            # stress tick on an already-afflicted hero re-ran
            # _apply_affliction_effects and re-logged "is now afflicted!".
            # That was invisible while AFFLICTION_DEFAULT/VIRTUE_DEFAULT
            # resolved to nothing, but it would stack the real personality
            # buffs (below) once per stress tick.
            was_resolved = target.is_afflicted or target.is_virtuous
            apply_stress(target, amount * effective_mult, self.rules, self.rng)
            # amount can be negative (crit relief, companion relief) — the
            # free apply_stress() has no floor, so clamp here rather than
            # letting stress go negative (would corrupt stress_frac-style
            # normalization downstream).
            target.stress = max(0, target.stress)
            if (target.is_afflicted or target.is_virtuous) and not was_resolved:
                status = "afflicted" if target.is_afflicted else "virtuous"
                self.log.append(f"{target.name} is now {status}!")
                self._apply_affliction_effects(target)
            if target.is_afflicted and target.stress >= HEART_ATTACK_STRESS_THRESHOLD:
                self._trigger_heart_attack(target)
            elif target.is_virtuous and target.stress >= HEART_ATTACK_STRESS_THRESHOLD:
                # Wiki-verified: a Virtuous hero who hits 200 doesn't heart-
                # attack — they lose Virtue and reset to 0 instead.
                target.is_virtuous = False
                target.stress = 0
                self.log.append(f"{target.name}'s Virtue fades")

    def _overstress_override(self, hd) -> list:
        """This hero's `overstressed_modifier:` record, resolved to
        `(trait_id, weight, is_virtue)` against the loaded traits.

        Resolved HERE rather than in `engine/stress.py` because that module
        has no store, and an id naming no loaded trait is dropped -- an
        override that resolves to nothing must fall back to the ordinary
        Resolve Check, not to no outcome at all.
        """
        out = []
        for i, tid in enumerate(getattr(hd, "overstress_override_ids", []) or []):
            trait = self.store.traits.get(tid)
            if trait is None:
                continue
            chances = getattr(hd, "overstress_override_chances", []) or []
            weight = float(chances[i]) if i < len(chances) else 1.0
            out.append((tid, weight, bool(trait.is_virtue)))
        return out

    def _draw_trait(self, hero: CombatantState) -> Optional[TraitDefinition]:
        """Pick the personality a Resolve Check turned this hero into.

        Uniform over the ROLLABLE traits of the right `overstress_type`, which
        matches the real base weighting (every trait starts at weight 4; the +2
        per-occurrence "affliction history" skew is a persistent campaign
        system across expeditions that this simulator doesn't model).

        Rollability is the game's own flag, not a guess — `TraitDefinition.
        is_rollable`. It excludes the `test` placeholder and the Flagellant's
        `berserk` (both `is_generated: False`; berserk comes from a specific
        Crimson Court trigger, not from a failed Resolve Check) and
        `color_madness`. That leaves the 7 afflictions and 5 virtues the wiki
        documents.
        """
        # An `overstressed_modifier:` hero already has their outcome; the
        # pool is not consulted and no draw is taken. `is_rollable` below is
        # unchanged and still correct -- berserk is `is_generated: False`
        # precisely because it is never RANDOM, only ever overridden to.
        if hero.forced_trait_id:
            return self.store.traits.get(hero.forced_trait_id)
        want_virtue = hero.is_virtuous
        pool = sorted(t.id for t in self.store.traits.values()
                      if t.is_virtue == want_virtue and t.is_rollable)
        if not pool:
            return None
        return self.store.traits[self.rng.choice(pool)]

    def _apply_affliction_effects(self, hero: CombatantState):
        """Give a Resolve Check outcome real mechanical teeth, from the real
        per-personality data in `shared/trait/trait_library.json`.

        History, because two successive versions of this were wrong:

        1. It first appended an ActiveBuff naming "AFFLICTION_DEFAULT" /
           "VIRTUE_DEFAULT" — ids in no data file anywhere, so affliction and
           virtue had ZERO stat effect.
        2. It then drew from `base.buffs.json` ids matching
           `^affliction(Name)Buff\\d+$` and added a hardcoded wiki "baseline"
           (-15 resists, -10% MAX HP) on top, documented here as a simulator
           convention because the real tables were believed to live in the
           binary. **They ship as data.** And the traits do not reference those
           `afflictionXBuffN` ids at all (the virtues do reference their
           `virtueXBuffN` ones), so every affliction was applying the wrong
           stat set: a Fearful hero really takes **-25% damage**, and instead
           got -10 ACC / -8 CRIT / +10 PROT.

        `TraitDefinition.buff_ids` is the COMPLETE effect list. The shared -15
        resists and MAXHP-10 are entries inside each affliction's own list, and
        the six +25 resists are entries inside every virtue's — which the old
        code missed entirely, leaving virtue far weaker than the real game's.
        So there is no baseline to add here any more; applying one would double
        it.
        """
        trait = self._draw_trait(hero)
        if trait is not None:
            if hero.is_virtuous:
                hero.virtue_id = trait.id
            else:
                hero.affliction_id = trait.id
            hero.overstress_stats = _int_stat_snapshot(hero)
            for buff_id in trait.buff_ids:
                bd = self.store.buffs.get(buff_id)
                if bd:
                    self._apply_buff_to_stats(hero, bd)
                    hero.buffs.append(ActiveBuff(buff_id=buff_id, remaining_rounds=None))
            self.log.append(f"{hero.name} is {trait.id}")

        if hero.is_afflicted:
            self._maybe_force_mode_revert(hero)

    def _roll_act_out(self, hero: CombatantState) -> Optional[TraitActOut]:
        """Roll one row of the hero's trait `combat_start_turn_act_outs` table.

        Weights are normalised over the rows valid for THIS hero's class, never
        the raw table — `attack_friendly` ships as one row per class (Abusive
        has 19 of them), so normalising over everything would make Abusive act
        out 64.7% of turns instead of its real 29.4%. See
        `TraitDefinition.act_outs_for`.

        Returns None when the roll lands on `nothing` (or the hero has no
        resolved trait), meaning the submitted action goes ahead untouched.
        """
        trait_id = hero.virtue_id if hero.is_virtuous else hero.affliction_id
        trait = self.store.traits.get(trait_id or "")
        if trait is None:
            return None
        rows = trait.act_outs_for(hero.definition_id)
        total = sum(r.chance for r in rows)
        if total <= 0:
            return None
        roll = self.rng.random() * total
        cursor = 0.0
        for row in rows:
            cursor += row.chance
            if roll < cursor:
                return None if row.kind == "nothing" else row
        return None

    def _maybe_act_out(self, hero: CombatantState) -> bool:
        """An overstressed hero does something other than what was ordered.

        Returns True when the submitted action is discarded and the turn is
        burned. Virtue act-outs return False: they are `.queue true` bonuses
        that fire alongside the hero's real action rather than replacing it.

        **This used to be a flat 33% chance of one single outcome** — always
        `bark_stress`, +6 to the whole party — documented in the previous
        version as a simulator convention because "the real per-personality
        act-out tables live in the game binary, not in any data file". They
        ship in `shared/trait/trait_library.json`, and they say something quite
        different: the *rate* was about right (29-35% across the afflictions),
        but only ~10% of an afflicted hero's turns are a party-stress bark.
        Fearful barks on 4 of 38 weight, not 38 of 38. Charging every act-out
        as a bark manufactured roughly **3.3x the real affliction stress**,
        directly on top of the mechanism that decides whether a long expedition
        survives — while the disruptive outcomes the real game actually uses
        (breaking formation, attacking an ally, obeying the wrong order) did
        not exist at all.

        Virtues fared worse still: they had no per-turn effect whatsoever,
        against a real 25%/turn chance of a stress heal or a party buff.
        """
        if not (hero.is_afflicted or hero.is_virtuous):
            return False
        if self._act_out_rolled_for_index == self.current_turn_index:
            return False
        self._act_out_rolled_for_index = self.current_turn_index
        # No two act-outs back to back -- see `_last_act_out_at`. Checked
        # BEFORE the roll so a suppressed turn does not consume one, which is
        # what "cannot happen" means rather than "happens and is discarded".
        last_round, last_index = self._last_act_out_at
        if last_round == self.round and last_index == self.current_turn_index - 1:
            return False
        if (last_round == self.round - 1
                and self.current_turn_index == 0
                and last_index == len(self.turn_order) - 1):
            return False
        if self._last_act_out_round_by_hero.get(hero.id, -99) == self.round - 1:
            return False
        row = self._roll_act_out(hero)
        if row is None:
            return False
        self._last_act_out_at = (self.round, self.current_turn_index)
        self._last_act_out_round_by_hero[hero.id] = self.round
        self._act_out_counts[row.kind] = self._act_out_counts.get(row.kind, 0) + 1
        handler = self._ACT_OUT_HANDLERS.get(row.kind)
        if handler is None:
            # Same rule as engine/camping.py's _requirements_met: an outcome
            # this simulator does not understand BLOCKS rather than guessing.
            # Silently treating it as a wasted turn would be inventing a
            # mechanic and calling it game data.
            self.vlog(f"    [ACT-OUT] unhandled kind {row.kind!r} on {hero.name} — ignored")
            return False
        name = hero.virtue_id or hero.affliction_id or "overstressed"
        self.log.append(f"{hero.name} ({name}): {row.kind}")
        return handler(self, hero, row)

    def _act_out_bark_stress(self, hero: CombatantState, row: TraitActOut) -> bool:
        """The outburst that stresses the party — `BarkStress` in
        effects/base.effects.darkest is `.chance 100% .stress 6`, which is
        where the old hardcoded 6.0 came from. Now it is the ~10% outcome the
        data says it is instead of the only one."""
        self._resolve_act_out_effect(hero, row.string_value or "BarkStress",
                                      targets=[a for a in self.heroes
                                               if a.is_alive() and a is not hero])
        return True

    def _act_out_ignore_command(self, hero: CombatantState, row: TraitActOut) -> bool:
        """Refuses the order outright. Costs the turn and nothing else — the
        outcome the old flat-bark model had no way to express."""
        return True

    def _act_out_change_pos(self, hero: CombatantState, row: TraitActOut) -> bool:
        """Shuffles rank instead of acting. Tactically this is the expensive
        one: it can strand a back-rank hero at the front where none of their
        skills launch. `number_value` is how far (always 1 in shipped data)."""
        distance = max(1, int(row.number_value or 1))
        occupied = sorted(h.position for h in self.heroes if h.is_alive())
        if len(occupied) < 2:
            return True
        options = [p for p in occupied if p != hero.position
                   and abs(p - hero.position) <= distance]
        if not options:
            return True
        swap_to = self.rng.choice(options)
        other = next(h for h in self.heroes if h.is_alive() and h.position == swap_to)
        hero.position, other.position = other.position, hero.position
        self.log.append(f"{hero.name} shuffles to rank {hero.position + 1}, "
                        f"displacing {other.name}")
        return True

    def _act_out_random_command(self, hero: CombatantState, row: TraitActOut) -> bool:
        """Obeys, but picks its own skill and target. Runs through the same
        get_valid_actions/_execute_action path the policy uses, so it can never
        produce an action the engine would otherwise reject."""
        options = self.get_valid_actions(hero.id)
        # `__retreat__` is excluded for a data reason, not a taste one: the real
        # game has its own `retreat_from_combat` act-out and ships it at
        # **weight 0 in every trait**, so an afflicted hero never flees. Letting
        # the random-command act-out reach it through the back door would
        # introduce exactly the behaviour the shipped tables switch off.
        options = [a for a in options
                   if a.skill_id not in ("__pass__", "__retreat__")]
        if not options:
            return True
        choice = options[self.rng.randint(0, len(options) - 1)]
        self.log.append(f"{hero.name} ignores the order and uses {choice.skill_id}")
        self._execute_action(choice)
        return True

    def _act_out_attack_friendly(self, hero: CombatantState, row: TraitActOut) -> bool:
        victims = [a for a in self.heroes if a.is_alive() and a is not hero]
        if not victims:
            return True
        return self._act_out_strike(hero, self.rng.choice(victims), row)

    def _act_out_attack_self(self, hero: CombatantState, row: TraitActOut) -> bool:
        return self._act_out_strike(hero, hero, row)

    def _act_out_mark_self(self, hero: CombatantState, row: TraitActOut) -> bool:
        """Masochistic and Depressed paint a target on their own back, which
        is the whole flavour: monster brains weight Marked heroes up, so this
        is a genuine self-inflicted cost.

        The mark is applied HERE rather than by loosening
        `_apply_effect_to_target`'s guard. That guard requires
        `eff.target == "target"` because 11 of the 30 real `.tag 1` effects
        target "performer" and are conditional self-buffs ("Leper Mark Self",
        "Crusader Bulwark Mark") rather than marks — flagging their performer
        as Marked was a real bug this repo already fixed once. "Mark Self" is
        the genuine exception, so it gets a narrow path instead of a wider
        rule."""
        name = row.string_value or "Mark Self"
        self._resolve_act_out_effect(hero, name, targets=[hero])
        eff = self.store.effects.get(name)
        if eff is not None and eff.is_mark_effect:
            hero.is_marked = True
            hero.mark_rounds_remaining = eff.duration or 3
            self.log.append(f"{hero.name} marks themselves!")
        return True

    def _act_out_stress_heal_self(self, hero: CombatantState, row: TraitActOut) -> bool:
        self._resolve_act_out_effect(hero, row.string_value, targets=[hero])
        return False

    def _act_out_stress_heal_party(self, hero: CombatantState, row: TraitActOut) -> bool:
        self._resolve_act_out_effect(hero, row.string_value,
                                      targets=[h for h in self.heroes if h.is_alive()])
        return False

    def _act_out_buff_party(self, hero: CombatantState, row: TraitActOut) -> bool:
        self._resolve_act_out_effect(hero, row.string_value,
                                      targets=[h for h in self.heroes if h.is_alive()])
        return False

    def _act_out_buff_random_party_member(self, hero: CombatantState, row: TraitActOut) -> bool:
        allies = [h for h in self.heroes if h.is_alive()]
        if allies:
            self._resolve_act_out_effect(hero, row.string_value,
                                          targets=[self.rng.choice(allies)])
        return False

    def _act_out_heal_self(self, hero: CombatantState, row: TraitActOut) -> bool:
        """Vigorous is the one virtue act-out with no effect name attached, so
        the amount is a declared SIMULATOR CONVENTION rather than game data:
        the same fraction of max HP that the trait's own `heal_self` implies
        elsewhere is not recorded anywhere, so this heals the party's smallest
        real heal step. Documented in ASSUMPTIONS.md."""
        amount = max(1, int(round(hero.max_hp * VIRTUE_SELF_HEAL_MAX_HP_FRACTION)))
        self._apply_heal(hero, amount, hero)
        return False

    _ACT_OUT_HANDLERS = {
        "bark_stress": _act_out_bark_stress,
        "ignore_command": _act_out_ignore_command,
        "change_pos": _act_out_change_pos,
        "random_command": _act_out_random_command,
        "attack_friendly": _act_out_attack_friendly,
        "attack_self": _act_out_attack_self,
        "mark_self": _act_out_mark_self,
        "stress_heal_self": _act_out_stress_heal_self,
        "stress_heal_party": _act_out_stress_heal_party,
        "buff_party": _act_out_buff_party,
        "buff_random_party_member": _act_out_buff_random_party_member,
        "heal_self": _act_out_heal_self,
    }

    def _act_out_strike(self, hero: CombatantState, victim: CombatantState,
                        row: TraitActOut) -> bool:
        """An act-out attack auto-hits for `number_value` of the attacker's own
        damage (0.05 for an Antiquarian, 0.33 for a Leper) and then applies the
        row's named effect — which is why an Abusive Crusader's ally-hit stuns
        and an Abusive Hellion's bleeds. No accuracy roll: the game does not
        give a hero the chance to miss their own friend."""
        fraction = row.number_value or 0.0
        damage = max(1, int(round(hero.dmg_max * fraction))) if fraction > 0 else 0
        if damage:
            if victim.damage_blocks > 0:
                victim.damage_blocks -= 1
                damage = 0
            else:
                damage = max(1, int(round(damage * (1.0 - victim.prot))))
                victim.hp -= damage
                self.log.append(f"{hero.name} strikes {victim.name} for {damage}")
                if victim.hp <= 0:
                    self._handle_deaths_door(victim, hero)
        if row.string_value:
            self._resolve_act_out_effect(hero, row.string_value, targets=[victim])
        return True

    def _resolve_act_out_effect(self, performer: CombatantState, effect_name: str,
                                 targets: list[CombatantState]):
        """Route an act-out's named effect through the engine's normal effect
        machinery. Every name the trait tables reference resolves in
        effects/base.effects.darkest, which data/effect_loader.py already
        parses into store.effects — so stress, buffs, bleed, blight, stun and
        marks all behave exactly as they would from a skill."""
        if not effect_name:
            return
        eff = self.store.effects.get(effect_name)
        if eff is None:
            self.vlog(f"    [ACT-OUT] effect {effect_name!r} not in store.effects — skipped")
            return
        for target in targets:
            if target.is_alive():
                self._resolve_effect(target, performer, eff, None)

    def _maybe_force_mode_revert(self, hero: CombatantState):
        """Real data: only Abomination's "beast" mode record sets
        .affliction_combat_skill_id (= "transform") — becoming Afflicted
        while in Beast form force-transforms him back to Human for free,
        distinct from a player-chosen Transform and NOT counted against
        his 2-per-battle Transform limit (_track_skill_use is deliberately
        not called here). Without this, an Abomination who already spent
        both charges and then gets Afflicted in Beast form would be stuck
        there (Rake/Rage/Slam only) for the rest of the fight, unlike the
        real game."""
        hd = self._get_hero_def(hero.id)
        if not hd:
            return
        mode_def = next((m for m in hd.modes if m.id == hero.mode), None)
        if not mode_def or not mode_def.affliction_combat_skill_id:
            return
        skill = self.get_skill_data(mode_def.affliction_combat_skill_id, level=hero.skill_tier)
        if not skill:
            return
        self.log.append(f"{hero.name}'s affliction forces a return from {hero.mode} form")
        mode_effects = skill.mode_effects.get(hero.mode, [])
        for ename in mode_effects:
            eff = self.store.effects.get(ename)
            if eff:
                self._resolve_effect(hero, hero, eff, skill)
