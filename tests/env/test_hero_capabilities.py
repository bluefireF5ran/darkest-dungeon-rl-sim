"""The hero capability profile -- the repair for a falsifier that came back NO.

The 2026-08-23 observation rewrite dropped the 20-class one-hot on the argument
that mechanic payloads carry everything identity did. The held-out-class run
tested that and it failed decisively: **+46.9pp excess gap, CI [+37.2, +56.9],
17 of 17 held-out comps worse**, with the two arms level on SEEN comps -- so the
loss was specifically the unseen class, not general competence.

The diagnosis the code itself had already written down: the ENEMY slot got
`_enemy_capabilities` (that monster's whole moveset, max-pooled into the payload
vector) and the HERO slot never did. The policy could read the ACTING hero's
seven skill slots and knew nothing about what its other three ranks could DO --
"the rank behind me can heal" was not a fact about the board it could represent.

These tests pin the repair and, more importantly, pin the two things that make
it useful rather than merely present: the pools are split by WHO the skill is
aimed at, and the profile follows the equipped loadout rather than the class.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.gym_env import (DarkestDungeonEnv, HERO_HEAL_HEADROOM, HERO_THREAT_FORECAST,
                         HERO_SLOT_SCALARS, HERO_STATE_SCALARS,
                         SKILL_MECHANIC_FEATURES)

store = get_store()

M = SKILL_MECHANIC_FEATURES
HEAL = 4          # index of the heal-amount feature within a pool
GUARD = 10
ALLY, SELF, FOE = 0, 1, 2


def _env():
    e = DarkestDungeonEnv(store=store)
    e.reset(seed=0)
    return e


def _profile(env, hero_class, skill_ids=None):
    """The three pools for one hero class, as a dict of slices."""
    enemy = next(iter(store.enemies.values()))
    # `hero_skill_ids` is a LIST indexed by hero, not a dict -- passing a
    # dict makes `enumerate` yield the KEYS and silently restricts nothing.
    env.engine.reset([store.heroes[hero_class]] * 2, [enemy],
                     hero_skill_ids=[skill_ids, skill_ids] if skill_ids else None)
    caps = env._hero_capabilities(env.engine.heroes[0])
    return {side: caps[side * M:(side + 1) * M] for side in (ALLY, SELF, FOE)}


def test_the_slot_width_is_derived_and_self_consistent():
    """`HERO_SLOT_SCALARS` is state plus THREE pools. It is derived rather than
    written down for the same reason `ENEMY_SLOT_SCALARS` is: sizing it by hand
    is exactly how the enemy slot landed 6 short when the payload grew 39 -> 45
    underneath it."""
    # +HERO_HEAL_HEADROOM and +HERO_THREAT_FORECAST since 2026-08-24, both
    # appended AFTER the pools so the pool indices themselves did not move.
    assert (HERO_SLOT_SCALARS == HERO_STATE_SCALARS + 3 * M
            + HERO_HEAL_HEADROOM + HERO_THREAT_FORECAST)
    env = _env()
    for h in env.engine.heroes:
        assert len(env._hero_slot_vec(h, is_actor=False)) == HERO_SLOT_SCALARS
    empty = env._hero_slot_vec(None, is_actor=False)
    assert len(empty) == HERO_SLOT_SCALARS and not any(empty)
    print("  PASS test_the_slot_width_is_derived_and_self_consistent")


def test_a_healer_is_readable_from_the_ally_pool():
    """The whole point, in one assertion. A Vestal can heal ANOTHER rank; a
    Leper can only heal himself. Pooled into one vector both read as
    "heal ~0.3" and the policy cannot tell a healer from a self-sustainer,
    which is precisely the fact a party needs about its third rank."""
    env = _env()
    vestal = _profile(env, "vestal")
    leper = _profile(env, "leper")

    assert vestal[ALLY][HEAL] > 0, "the Vestal's ally heal is not visible"
    assert leper[ALLY][HEAL] == 0, "the Leper reads as able to heal an ally"
    assert leper[SELF][HEAL] > 0, "the Leper's self heal vanished entirely"
    print("  PASS test_a_healer_is_readable_from_the_ally_pool")


def test_a_self_only_skill_is_not_filed_under_the_enemy():
    """The trap this split exists to avoid, and the one a two-way split on
    `targets_allies` alone falls into. A self-only skill carries
    `targets_allies False` AND an EMPTY `.target`, so keying on the flag alone
    files Solemnity and Lick Wounds under FOE -- which claims the hero can heal
    the monsters."""
    for cls in ("leper", "houndmaster"):
        selfers = [sk.id for sk in store.heroes[cls].skills
                   if not sk.levels[max(sk.levels)].target
                   and not sk.levels[max(sk.levels)].targets_allies]
        assert selfers, f"{cls} has no self-only skill; fixture assumption broke"

    env = _env()
    hm = _profile(env, "houndmaster")
    assert hm[SELF][HEAL] > 0, "Lick Wounds did not land in the SELF pool"
    assert hm[FOE][HEAL] == 0, "a self-heal read as healing the enemy"
    print("  PASS test_a_self_only_skill_is_not_filed_under_the_enemy")


def test_guard_lands_on_the_ally_side():
    """Guard Dog is `targets_allies` with a real target list and
    `self_target_valid False` -- the Houndmaster cannot guard himself. It is the
    clearest non-heal case of "this rank can do something FOR me"."""
    env = _env()
    hm = _profile(env, "houndmaster")
    assert hm[ALLY][GUARD] == 1.0, "Guard Dog is not visible on the ally side"
    assert hm[FOE][GUARD] == 0.0
    print("  PASS test_guard_lands_on_the_ally_side")


def test_the_profile_follows_the_EQUIPPED_kit_not_the_class():
    """Read from `get_hero_skill_ids` (the restricted 4-of-7), never the full
    movepool: two Vestals running different kits genuinely differ in what they
    can do, and `--loadout-random-p` varies exactly that. A profile keyed on the
    class would tell the policy a Vestal who did not bring a heal can heal."""
    env = _env()
    no_heal = _profile(env, "vestal",
                       ["mace_bash", "judgement", "dazzling_light",
                        "gods_hand"])
    with_heal = _profile(env, "vestal",
                         ["mace_bash", "judgement", "divine_grace",
                          "gods_comfort"])
    assert no_heal[ALLY][HEAL] == 0, (
        "a Vestal with no heal equipped still reads as a healer")
    assert with_heal[ALLY][HEAL] > 0
    print("  PASS test_the_profile_follows_the_EQUIPPED_kit_not_the_class")


def test_classes_are_distinguishable_without_an_identity_block():
    """The claim the one-hot removal rests on. Identity is gone; what replaces
    it has to actually separate the roster, or three of the four ranks are
    interchangeable stat blocks again.

    ONE collision is expected and is not a failure -- it is the clearest
    evidence the encoding does what it was asked to do. The Musketeer is the
    Arbalest reskinned: seven differently-NAMED skills (`aimed_shot` for
    `sniper_shot`, `patch_up` for `battlefield_bandage`) carrying the same
    numbers. Under the old one-hot they were two unrelated indices and the
    policy had to learn each separately; under a capability profile they are
    the same hero, which is exactly the transfer the rewrite is for.
    """
    env = _env()
    seen = {}
    for cls in sorted(store.heroes):
        if not store.heroes[cls].skills:
            continue
        caps = _profile(env, cls)
        key = tuple(round(x, 4) for side in (ALLY, SELF, FOE) for x in caps[side])
        seen.setdefault(key, []).append(cls)
    collisions = sorted(tuple(v) for v in seen.values() if len(v) > 1)
    assert collisions == [("arbalest", "musketeer")], (
        f"unexpected profile collisions: {collisions}")
    print(f"  PASS test_classes_are_distinguishable_without_an_identity_block "
          f"({len(seen)} distinct profiles, Arbalest == Musketeer by design)")


def test_the_memo_is_keyed_on_the_kit_and_the_tier():
    """It runs for 4 ranks on every env step, so it is memoised -- and the key
    has to carry the SKILL TIER as well as the ids, or a rank-0 party reads with
    rank-5 numbers because a maxed one was profiled first."""
    env = _env()
    h = env.engine.heroes[0]
    ids = tuple(env.engine.get_hero_skill_ids(h.id))
    h.skill_tier = 4
    high = list(env._hero_capabilities(h))
    h.skill_tier = 0
    low = list(env._hero_capabilities(h))
    assert (ids, 4) in env._hero_caps_memo and (ids, 0) in env._hero_caps_memo
    assert high != low, "the skill tier does not reach the profile"
    # ...and the memo must not hand out a list a caller can corrupt in place.
    again = env._hero_capabilities(h)
    assert again == low
    print("  PASS test_the_memo_is_keyed_on_the_kit_and_the_tier")


def test_the_pools_reach_the_observation():
    """A block that is computed and then not concatenated is the failure mode
    that costs a retrain to discover. Two dead features have already shipped
    here, and both were constants no width or finiteness check could see."""
    env = _env()
    vestal_vec = None
    for cls, want in (("vestal", True), ("leper", False)):
        enemy = next(iter(store.enemies.values()))
        env.engine.reset([store.heroes[cls]] * 4, [enemy])
        vec = env._hero_slot_vec(env.engine.heroes[0], is_actor=False)
        tail = vec[HERO_STATE_SCALARS:HERO_STATE_SCALARS + 3 * M]
        assert len(tail) == 3 * M
        assert (tail[HEAL] > 0) is want, f"{cls} ally-heal not in the slot tail"
        if cls == "vestal":
            vestal_vec = vec
    obs = env._get_obs()
    # The rank-0 hero block is the first HERO_SLOT_SCALARS floats.
    assert len(obs) == env.observation_space.shape[0]
    assert obs[HERO_STATE_SCALARS:HERO_STATE_SCALARS + 3 * M].any(), (
        "the capability tail is all zeros inside the real observation")
    assert vestal_vec is not None
    print("  PASS test_the_pools_reach_the_observation")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
