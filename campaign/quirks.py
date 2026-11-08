"""What a quest does to a hero's quirks and diseases on the way home.

The game's rule, from the binary's quest-end code (the `Inventory::System`
constructor is the one reader of these keys) and the shipped base
`rules.json` (`CombatRules`):

- per surviving hero, one NEGATIVE and one POSITIVE roll. Each chance is
  interpolated on the hero's stress, capped at 100, between the quest
  outcome's `*Stress0*` and `*Stress100*` value: a won quest at 0 stress is
  30% negative / 50% positive, a lost one at 100 is 70% / 25%;
- from resolve 2 on, a disease roll at `0.32 - disease resist x 0.33`, never
  below 0.05.

ASSUMPTIONS, each declared:
- a gained quirk is drawn uniformly from its polarity's pool (the library's
  `random_chance` is 1 or 0, and the 0s never come), skipping one the hero
  owns or that clashes with an owned quirk (`incompatible_quirks`, both ways);
- at the polarity's cap (`quirks_max_positive` / `_negative` 5,
  `quirks_max_diseases` 3) the new one replaces a uniformly drawn owned one:
  every library quirk ships `can_be_replaced_by_new_quirk` true; a quirk
  locked at the Sanitarium is never the one replaced, and with every owned
  one of the polarity locked the new quirk does not come;
- the disease resist is the class's base stat; trinket and quirk resist
  buffs are not counted;
- the combat's own disease effects (a monster attack's disease chance) are
  not rolled.
"""
from __future__ import annotations

import random
from typing import Optional

from campaign.recruits import unrandomisable_quirks

#: The rolls are on by default -- a fidelity fix, not a policy knob.
#: `--no-quest-quirks` turns them off for an A/B arm.
QUEST_QUIRKS = True


def _kind(q) -> str:
    return "disease" if q.is_disease else ("positive" if q.is_positive else "negative")


def _gain(rng: random.Random, store, quirks: list[str], kind: str, cap: int,
          locked=frozenset()) -> Optional[str]:
    """Add one random quirk of `kind` to `quirks` in place; the id, or None
    when the pool is empty or every owned one at the cap is locked."""
    never = unrandomisable_quirks()
    owned = set(quirks)
    clash = {c for q in quirks if q in store.quirks for c in store.quirks[q].incompatible_quirks}
    pool = sorted(q.id for q in store.quirks.values()
                  if _kind(q) == kind and q.id not in never and q.id not in owned
                  and q.id not in clash and not owned & set(q.incompatible_quirks))
    if not pool:
        return None
    same = [q for q in quirks if q in store.quirks and _kind(store.quirks[q]) == kind]
    if len(same) >= cap:
        free = [q for q in same if q not in locked]
        if not free:
            return None
        new = rng.choice(pool)
        quirks.remove(rng.choice(free))
    else:
        new = rng.choice(pool)
    quirks.append(new)
    return new


def quest_end(rng: random.Random, store, hero, *, success: bool,
              locked=frozenset()) -> list[str]:
    """Roll one surviving hero's quest-end quirks and disease, changing
    `hero.quirks` in place. Returns the ids gained."""
    r = store.rules
    o = "success" if success else "failure"
    t = min(max(float(hero.stress), 0.0), 100.0) / 100.0
    neg = getattr(r, f"neg_stress0_{o}") + (getattr(r, f"neg_stress100_{o}") - getattr(r, f"neg_stress0_{o}")) * t
    pos = getattr(r, f"pos_stress0_{o}") + (getattr(r, f"pos_stress100_{o}") - getattr(r, f"pos_stress0_{o}")) * t
    quirks = list(hero.quirks)
    gained = []
    # The binary draws the positive roll's number first, then the negative's,
    # and resolves the negative first.
    p_roll, n_roll = rng.random(), rng.random()
    if n_roll < neg and (q := _gain(rng, store, quirks, "negative", int(r.quirks_max_negative),
                                    locked)):
        gained.append(q)
    if p_roll < pos and (q := _gain(rng, store, quirks, "positive", int(r.quirks_max_positive),
                                    locked)):
        gained.append(q)
    if hero.resolve_level >= r.disease_after_quest_min_resolve_level:
        hd = store.heroes.get(hero.hero_class)
        resist = hd.base_stats.disease_resist if hd is not None else 0.0
        chance = max(r.disease_after_quest_min_chance,
                     r.disease_max_chance - resist * r.disease_hero_disease_resist_weight)
        if rng.random() < chance and (q := _gain(rng, store, quirks, "disease",
                                                  int(r.quirks_max_diseases))):
            gained.append(q)
    hero.quirks = quirks
    return gained
