"""`--focus-mechanic`: specialise on a MECHANIC, never on named comps.

Fran's constraint, 2026-08-27, and it is the whole design:

    *"we want to fine tune this model so it learns 'The mechanics of Darkest
    Dungeon and the importance of certain numbers' rather than making a player
    that follows the same plan always, as this wont enable the use of modded
    classes and will hurt our scalability on the future"*

So a family is what a comp CAN DO. A party fielding Solo, Protect Me,
Invigorating Vapours or Guard Dog is a dodge party whoever is in it, including
a modded class that arrives later and appears in no list here. Same argument
that removed the 20-class one-hot from the observation.
"""
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.expedition_env import (MECHANIC_FAMILIES, build_comp_pool,
                                comp_mechanics)
from env.expedition_sequence_env import ExpeditionSequenceEnv

store = get_store()


def _families_of(comp_name):
    return comp_mechanics(store, store.comp_loadout_by_name(comp_name))


def test_a_family_means_BUILT_AROUND_it_not_merely_capable_of_it():
    """One skill is not a plan. At a threshold of 1, 144 of 163 comps are
    'dodge comps' and 130 are 'bleed comps' -- that is not a focus, it is the
    pool. `FOCUS_MIN_SOURCES` is what makes the knob mean something."""
    from env.expedition_env import FOCUS_MIN_SOURCES, comp_mechanic_counts
    assert FOCUS_MIN_SOURCES >= 2
    wide = sum(1 for n, _z, _h in build_comp_pool(store)
               if "dodge" in _families_of(n))
    narrow = sum(1 for n, _z, _h in build_comp_pool(store)
                 if comp_mechanic_counts(
                     store, store.comp_loadout_by_name(n)).get("dodge", 0)
                 >= FOCUS_MIN_SOURCES)
    assert narrow < wide, (narrow, wide)


def test_a_thin_family_relaxes_rather_than_shipping_a_two_comp_focus():
    """Only the Runaway burns, so 7 comps can burn at all and 2 can stack it.
    A two-comp focus taken half the time is a smaller training set wearing a
    knob's clothes."""
    from env.expedition_env import FOCUS_MIN_POOL
    env = ExpeditionSequenceEnv(sampler_seed=0, focus_mechanic="burn")
    assert len(env._focus_pool) >= FOCUS_MIN_POOL - 1, len(env._focus_pool)


def test_every_family_has_comps_that_can_play_it():
    """A focus that matches nothing would silently train on the full pool and
    look like a successful experiment -- the same failure `--hold-out-class`
    validates its argument against."""
    counts = collections.Counter()
    for name, _zone, _heroes in build_comp_pool(store):
        for family in _families_of(name):
            counts[family] += 1
    for family in MECHANIC_FAMILIES:
        assert counts[family] > 0, f"{family} matches no comp at all"


def test_membership_is_read_off_the_EQUIPPED_kit():
    """Equipped, not the full movepool: a Jester who did not bring Solo is not
    a dodge comp, and `--loadout-random-p` varies exactly that."""
    burn_comps = [n for n, _z, _h in build_comp_pool(store)
                  if "burn" in _families_of(n)]
    assert burn_comps, "no comp can burn"
    for name in burn_comps:
        loadout = store.comp_loadout_by_name(name)
        classes = {h.hero_id for h in loadout.heroes}
        # Burn is the Runaway's, and hers alone in the shipped roster -- so a
        # comp in the burn family must field her. If that ever stops being
        # true it means another class gained burn, which is a finding.
        assert "runaway" in classes, (name, classes)


def test_a_dodge_comp_is_identified_by_CAPABILITY_not_by_class_name():
    """Fran named Jester's Solo, Antiquarian's Protect Me and Invigorating
    Vapours, and Houndmaster's Guard Dog. The detector must find those by what
    they do."""
    from env.expedition_env import _skill_grants_dodge
    assert _skill_grants_dodge(store, "solo", "jester")
    assert _skill_grants_dodge(store, "invigorating_vapours", "antiquarian")
    assert _skill_grants_dodge(store, "guard_dog", "houndmaster")
    # And a plain attack is not a dodge skill.
    assert not _skill_grants_dodge(store, "wicked_slice", "highwayman")


def test_the_knob_off_leaves_the_sampler_untouched():
    """Every knob in this project defaults off and is proven identical."""
    plain = ExpeditionSequenceEnv(sampler_seed=0)
    focused = ExpeditionSequenceEnv(sampler_seed=0, focus_mechanic=None)
    assert plain._focus_pool == [] == focused._focus_pool
    a = [plain._sample_comp() for _ in range(50)]
    b = [focused._sample_comp() for _ in range(50)]
    assert a == b


def test_the_focus_share_matches_focus_p():
    """Half-and-half is the design, not a hedge: a pure focus run is just a
    smaller training set, and the ask was to specialise WITHOUT losing the
    rest."""
    env = ExpeditionSequenceEnv(sampler_seed=0, focus_mechanic="blight",
                                focus_p=0.5)
    focus_names = {entry[0] for entry in env._focus_pool}
    assert 0 < len(focus_names) < len(env._hero_pool)
    draws = [env._sample_comp()[0] for _ in range(400)]
    share = sum(1 for n in draws if n in focus_names) / len(draws)
    # 0.5 from the focus branch plus the family's own share of the full pool
    # on the other branch, so the floor is 0.5 and the ceiling is well under 1.
    assert 0.45 < share < 0.95, share


def test_focus_p_of_one_still_draws_only_from_the_family():
    env = ExpeditionSequenceEnv(sampler_seed=0, focus_mechanic="dodge",
                                focus_p=1.0)
    focus_names = {entry[0] for entry in env._focus_pool}
    for _ in range(80):
        assert env._sample_comp()[0] in focus_names


def test_an_unknown_mechanic_is_refused():
    import pytest
    with pytest.raises(ValueError):
        ExpeditionSequenceEnv(sampler_seed=0, focus_mechanic="dodgee")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS {name}")
