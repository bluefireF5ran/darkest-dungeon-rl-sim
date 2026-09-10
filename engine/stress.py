from __future__ import annotations
from .combatant import CombatantState
from .rng import SeededRNG
from data.types import CombatRules


def apply_stress(target: CombatantState, amount: float, rules: CombatRules, rng: SeededRNG):
    if not target.is_hero:
        return
    target.stress += int(amount)
    if target.stress >= 100:
        resolve_check(target, rules, rng)


def resolve_check(combatant: CombatantState, rules: CombatRules, rng: SeededRNG):
    """The Resolve Check a hero makes on crossing 100 stress.

    Real mechanic (wiki: "Heroes enter an afflicted state upon reaching 100
    stress. The alternative outcome is receiving a Virtue instead") is a
    straight binary split, and the shipped rules.json values confirm it —
    affliction_base_onset_chance (0.75) + affliction_critical_success_chance
    (0.25) sum to exactly 1.0, i.e. two sides of one roll rather than two
    nested ones.

    This used to nest the virtue roll INSIDE the affliction roll, which gave
    P(affliction) = 0.75 * 0.75 = 56.25%, P(virtue) = 0.75 * 0.25 = 18.75%,
    and a spurious 25% "nothing happened, reset to 45" branch that has no
    counterpart in the real game — letting a hero bounce off 100 stress
    indefinitely and making the whole stress system far more forgiving than
    it should be.
    """
    if combatant.is_afflicted or combatant.is_virtuous:
        return
    if combatant.overstress_override:
        _resolve_override(combatant, rules, rng)
        return
    # `resolve_check_percent` (45 shipped buffs) shifts the VIRTUE side of the
    # split. Positive is better for the hero, which is what the carriers say:
    # `mercurial` is a positive quirk and carries +, while `ennui` and
    # `never_again_affliction` carry -.
    #
    # **An UNDERLEVELED hero is likelier to break** (wired 2026-09-02).
    # `effectiveDifficultyAfflictionOnsetModifiers` is indexed by the hero's
    # LEVEL SHORTFALL against the quest -- not by the quest's difficulty, which
    # is the reading that made this look unimplementable, because it puts
    # virtue at zero for every Champion party. By shortfall it decodes exactly
    # to the stated rule:
    #
    #   shortfall  0    1    2    3    4    5     6
    #   virtue    25%  20%  15%  10%   5%   0%   -8% -> clamped to 1%
    #
    # i.e. **-5% Virtue Chance per level of difference, and -33% at six** (a
    # resolve-0 hero in the Darkest Dungeon).
    #
    # **And the clamp IS the rules' own affliction min/max, inverted.** An
    # earlier version of this comment said they were "stated for the affliction
    # chance, not this one, so they are deliberately not reused here" -- and the
    # numbers say otherwise: `affliction_max_chance` 0.99 and
    # `affliction_min_chance` 0.05 give a virtue chance bounded by **1% and
    # 95%**, which is the documented floor and ceiling to the point. Two sides
    # of one roll share one pair of bounds.
    table = rules.difficulty_affliction_onset_modifiers or ()
    penalty = table[min(combatant.level_shortfall, len(table) - 1)] if table else 0.0
    virtue_chance = (rules.affliction_critical_success_chance
                     + combatant.resolve_check_mod - penalty)
    virtue_chance = min(1.0 - rules.affliction_min_chance,
                        max(1.0 - rules.affliction_max_chance, virtue_chance))
    if rng.random() < virtue_chance:
        combatant.is_virtuous = True
        combatant.is_afflicted = False
        # Real value, not 0 — rules.json stress_affliction_critically_resisted
        # (45; stress_affliction_resisted holds the same 45 for the same
        # outcome). Resetting to 0 handed back the entire stress bar.
        combatant.stress = int(rules.stress_affliction_critically_resisted)
    else:
        combatant.is_afflicted = True
        combatant.is_virtuous = False
        combatant.stress = max(combatant.stress, 100)


def _resolve_override(combatant: CombatantState, rules: CombatRules, rng: SeededRNG):
    """A hero whose own `.info.darkest` carries `overstressed_modifier:` does
    not take the Resolve Check at all -- the record names the outcome.

    DECLARED READING, on two pieces of evidence. The record sits on
    `overstressed_modifier`, which names the whole overstress event rather
    than the affliction half of it; and its `_ids`/`_chances` pair has the
    same shape as `.random_monster_class_ids`/`_chances`, four entries away in
    the same field table, where the chances are WEIGHTS over a draw that
    always happens rather than a gate on whether it happens. Read that way a
    present record always fires, so this hero can never be virtuous.

    THE OTHER READING, recorded because implementing the wrong one is a
    regression: the override could replace only the AFFLICTION side, leaving
    the 25% virtue roll intact. It is the weaker reading -- it makes the
    `chances 1.0` redundant with the pool weights it is replacing, and it does
    not explain why the key is on `overstressed_modifier` rather than on
    something named for afflictions.

    Falsifier, and it is cheap: `-startafflicted` forces the state, but the
    honest test is the other way round -- run a Flagellant to 100 stress
    repeatedly under `-randomseed` and count virtues. Any virtue at all
    falsifies this reading; ~25% of them confirms the other one.

    No draw is taken for a single-entry override. That is the
    `dot_irresistible` precedent: rolling against a foregone conclusion gives
    the same outcome and a different RNG stream, which invalidates every trace
    comparison after it. The shipped data has exactly one entry.
    """
    entries = combatant.overstress_override
    if len(entries) == 1:
        tid, _weight, is_virtue = entries[0]
    else:
        total = sum(max(0.0, w) for _i, w, _v in entries)
        if total <= 0.0:
            tid, _weight, is_virtue = entries[0]
        else:
            roll = rng.random() * total
            acc = 0.0
            tid, _weight, is_virtue = entries[-1]
            for cand, w, v in entries:
                acc += max(0.0, w)
                if roll < acc:
                    tid, is_virtue = cand, v
                    break
    combatant.forced_trait_id = tid
    combatant.is_virtuous = bool(is_virtue)
    combatant.is_afflicted = not combatant.is_virtuous
    if combatant.is_virtuous:
        combatant.stress = int(rules.stress_affliction_critically_resisted)
    else:
        combatant.stress = max(combatant.stress, 100)
