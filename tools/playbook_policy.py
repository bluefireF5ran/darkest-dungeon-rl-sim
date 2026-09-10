"""A scripted combat player built from `docs/PLAYBOOK.md`.

**Why this exists.** `tools/comp_ga.py` scores a comp by
`P(this policy completes the run)`, which makes the fitness a *biased*
estimator: it rewards comps that are easy for THIS policy, not comps that are
good. A comp whose strength needs a play the policy never learned reads as a
bad comp, and nothing inside a single-model search can tell those apart. That
already cost this project a real investigation -- the Vestal holding
Illumination was the fitness faithfully reporting the policy's habits, and the
cause turned out to be an engine gap.

`--model-b` answers it with a second checkpoint, which helps but does not
escape: two RL policies trained on the same distribution share most of the same
blind spots. This is the arm with no neural network in it at all. It plays
Fran's written criteria, interviewed 2026-08-14, before any of these comps
existed and never fitted to anything.

**It is a SECOND OPINION, not a better one.** A scripted player encodes our
theory of good play, so a comp scoring well under it may simply be a comp that
suits the script. The value is that it fails *differently* from a learned
policy -- a comp both like is a much stronger claim than a comp either likes.

**Interface.** It quacks like a `MaskablePPO`: `predict(obs, deterministic=,
action_masks=) -> (action, None)`, so it drops into
`run_full_expedition_episode` unchanged. It ignores `obs` entirely and reads
the live engine, because the playbook's rules are about board state ("every
living enemy is stunned", "already dying to its own bleed") and recovering
those from a 353-float vector would be a reimplementation with its own bugs.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from engine.resolution import compute_hit_chance
from env.gym_env import _is_doomed

# PLAYBOOK 3.2: heal only in "situaciones extremas o en stall". `check_playbook`
# already reads the extreme as Death's Door or HP under a third and grades the
# live policy against exactly that, so the two agree by construction.
CRITICAL_HP_FRACTION = 0.34

# PLAYBOOK 3.5: buffs "normalmente no compensan", and the measured reason is the
# regime -- combats run a median of 2 rounds here and 3 in real captures, so a
# buff rarely has time to pay for itself. It is not "never": the playbook's own
# exception is long fights and bosses, and this is the round by which the fight
# has demonstrated it is one.
LONG_FIGHT_ROUND = 4

# The Resolve Check fires at 100 stress and an affliction lasts the rest of the
# raid, so the band just below it is where a stress heal is still worth a turn.
RESOLVE_CHECK_STRESS = 100

# Imported, never re-typed. These were bare literals `36` and `37`, the only
# place outside env/gym_env.py that hardcoded the action layout -- so widening
# MAX_SKILL_SLOTS, RANKS or PROVISION_ACTION_ORDER would have made this policy
# return a skill action where it meant "pass", silently.
from env.gym_env import ACTION_PASS as PASS_ACTION
from env.gym_env import ACTION_RETREAT as RETREAT_ACTION


class PlaybookPolicy:
    """Plays `docs/PLAYBOOK.md` section 3. No weights, nothing learned."""

    def __init__(self, env):
        self.env = env

    # -- board reading -----------------------------------------------------
    @staticmethod
    def _hp_fraction(c) -> float:
        return c.hp / c.max_hp if c.max_hp else 0.0

    def _needs_recovery(self, engine) -> bool:
        """PLAYBOOK 3.2/3.3's "extreme situation", read party-wide."""
        return any(h.is_deaths_door or self._hp_fraction(h) < CRITICAL_HP_FRACTION
                   for h in engine.heroes if h.is_alive())

    def _classify(self, engine, hero, action):
        """What KIND of play is this legal action? Returns (tag, target)."""
        data = engine.get_skill_data(action.skill_id, level=hero.skill_tier)
        if data is None:
            return "other", None
        target = next((c for c in list(engine.heroes) + list(engine.enemies)
                       if c.id == action.target_id), None)
        if engine._is_ally_skill(action.skill_id):
            if data.heal_min is not None:
                return "heal", target
            for name in data.effects:
                eff = engine.store.effects.get(name)
                if eff is not None and eff.stress_heal:
                    return "stress_heal", target
            return "buff", target
        for name in data.effects:
            eff = engine.store.effects.get(name)
            if eff is not None and eff.stun_chance is not None:
                return "stun", target
        return "attack", target

    # -- the policy --------------------------------------------------------
    def predict(self, obs, deterministic=True, action_masks=None, **_kw):
        env = self.env.unwrapped
        engine = env.engine
        hero_id = engine.get_current_actor_id()
        if action_masks is not None:
            legal = [i for i, ok in enumerate(action_masks) if ok]
        else:
            legal = list(env._action_map)
        return np.array(self._choose(engine, hero_id, legal)), None

    def _choose(self, engine, hero_id, legal) -> int:
        env = self.env.unwrapped
        if hero_id is None or not legal:
            return PASS_ACTION
        hero = engine.heroes[hero_id]
        # Retreat is never scripted: PLAYBOOK 2.6 prices it in town gold, a
        # layer this env cannot see, so the script has no basis for taking it.
        options = [(i, *self._classify(engine, hero, env._action_map[i]))
                   for i in legal if i in env._action_map]
        if not options:
            return PASS_ACTION if PASS_ACTION in legal else legal[0]

        living = [e for e in engine.enemies if e.is_alive()]

        # 3.2 -- heal, but only into an extreme. Death's Door first, then the
        # lowest fraction under a third. Anything above that is the reactive
        # antipattern the playbook names and `check_playbook` measures.
        wounded = [h for h in engine.heroes
                   if h.is_alive() and (h.is_deaths_door
                                        or self._hp_fraction(h) < CRITICAL_HP_FRACTION)]
        if wounded:
            worst = min(wounded, key=lambda h: (not h.is_deaths_door,
                                                self._hp_fraction(h)))
            for idx, kind, target in options:
                if kind == "heal" and target is not None and target.id == worst.id:
                    return idx

        # 3.2, the other axis. The rule says heal only in an extreme, and a
        # hero one bad hit from a Resolve Check IS one: an affliction lasts the
        # rest of the raid, so the stress that causes it is not a per-combat
        # cost. Measured need -- without this the script finished expeditions
        # at 167.7 mean stress against the policy's 76.4, and afflicted parties
        # are most of why it was losing heroes.
        stressed = [h for h in engine.heroes
                    if h.is_alive() and h.stress >= RESOLVE_CHECK_STRESS - 20]
        if stressed:
            worst = max(stressed, key=lambda h: h.stress)
            for idx, kind, target in options:
                if kind == "stress_heal" and (target is None or target.id == worst.id
                                              or target.is_hero):
                    return idx

        # 3.3 -- action economy on a secured kill. Every living enemy stunned
        # and the party wanting recovery: the kill is already guaranteed and
        # finishing it is someone else's turn, so spend this one recovering.
        if living and all(e.is_stunned for e in living) and self._needs_recovery(engine):
            for want in ("stress_heal", "heal"):
                for idx, kind, _t in options:
                    if kind == want:
                        return idx

        # 3.1/3.6/3.7 -- offence. Threat first, control counted as worth taking
        # on threat alone, and a secured kill preferred over a bigger swing.
        attacks = [(i, k, t) for i, k, t in options
                   if k in ("attack", "stun") and t is not None and not t.is_corpse]
        # DOT -- an enemy already dying to its own bleed or blight is not worth
        # a turn. Skip them unless they are all that is left.
        undoomed = [o for o in attacks if not _is_doomed(o[2])]
        pool = undoomed or attacks
        if pool:
            return max(pool, key=lambda o: self._offence_score(engine, hero, o))[0]

        # CRP -- nothing living is reachable. A corpse standing in front of a
        # living enemy is what is actually blocking reach, so break that one.
        corpses = [(i, k, t) for i, k, t in options
                   if t is not None and t.is_corpse
                   and any(e.is_alive() and e.position > t.position
                           for e in engine.enemies)]
        if corpses:
            return corpses[0][0]

        # 3.5 -- a buff is the last resort, and only once the fight has proven
        # to be a long one. Otherwise it never amortises.
        if engine.round >= LONG_FIGHT_ROUND:
            for idx, kind, _t in options:
                if kind == "buff":
                    return idx
        non_retreat = [i for i, _k, _t in options if i != RETREAT_ACTION]
        return non_retreat[0] if non_retreat else PASS_ACTION

    def _offence_score(self, engine, hero, option) -> tuple:
        """Bigger is better. The ORDER of these keys is the playbook; the
        magnitudes are the engine's own numbers. Nothing here is tuned.

        The first version scored only the TARGET, so every option aimed at the
        same enemy tied and `max` fell through to the lowest skill slot -- the
        Duelist opened with Touche 57 times in two episodes and the Arbalest
        led with Bola instead of Sniper Shot. Which skill is a real decision,
        not a tiebreak.
        """
        idx, kind, target = option
        env = self.env.unwrapped
        action = env._action_map[idx]
        data = engine.get_skill_data(action.skill_id, level=hero.skill_tier)
        if data is None:
            return (False, False, 0.0, 0.0)
        try:
            targets = engine._resolve_action_targets(action, data, False)
        except Exception:                                        # noqa: BLE001
            targets = [target]
        targets = [t for t in targets if t is not None] or [target]

        # Expected damage, through the engine's own accuracy rule -- PLAYBOOK
        # 3.7 prefers the safe play, and a 40%-to-hit swing for double damage
        # is exactly what it warns about. An AoE is worth what it actually
        # lands on, which is counting rather than a bonus.
        base = (hero.dmg_min + hero.dmg_max) / 2.0 * (1.0 + data.dmg_mod)
        total = 0.0
        kills = 0
        threat = 0.0
        for t in targets:
            hit = compute_hit_chance(hero, t, data, engine.store.rules)
            total += base * max(0.0, min(1.0, hit))
            kills += base >= t.hp
            threat = max(threat, float(t.dmg_max))
        # 3.7: the play that secures a kill. 3.6: "casi todo aturdimiento es
        # netamente positivo", most of all against the dangerous one -- so a
        # stun is worth taking on threat alone, where damage is worth taking
        # on what it finishes.
        # Focus fire, and it is load-bearing rather than a tiebreak. Enemies of
        # one kind share a `dmg_max`, so threat ties constantly and the choice
        # fell through to whichever rank came first -- the party spread damage
        # across the board, nothing died, fights ran long and the stress that
        # killed the party was the interest on that. Finishing the weakest is
        # the action economy 3.3 is reasoning about in the first place.
        weakest = -min(t.hp for t in targets)
        return (kills, kind == "stun", threat, total, weakest)
