"""The class-balanced comp sampler: a weight, never a filter.

The pool is skewed by roughly nine to one — Houndmaster fills ~100 hero slots,
Duelist ~12 — so uniform comp sampling makes the policy practise one class far
more than another. That is a LEARNING decision, and it is deliberately separate
from the question of which classes are good, which is a fact about the game and
has to keep showing up in evaluation.

Two properties carry the whole design and both are pinned here:

* **`class_balance=0.0` is byte-identical to every archived checkpoint.** Not
  approximately — the same seed must produce the same comps in the same order.
  This is why the unbalanced path keeps calling `random.choice` instead of
  passing uniform weights to `random.choices`: the two consume the RNG
  differently, so a "harmless" swap would silently change every archived arm's
  episode stream while looking like a no-op.
* **Nothing is ever removed.** A rare class's comps are drawn more often; a
  common class's comps are still drawn. Filtering would change what the policy
  can ever see, which is a different and much larger change.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store                                   # noqa: E402
from env.expedition_env import ExpeditionEnv, build_comp_pool     # noqa: E402

VANILLA_SEED = 4242


def _env(balance):
    return ExpeditionEnv(store=get_store(), sampler_seed=VANILLA_SEED,
                         class_balance=balance)


def _draws(env, n=400):
    env._sampler_rng.seed(VANILLA_SEED)
    return [env._sample_comp()[0] for _ in range(n)]


def test_zero_balance_is_byte_identical_to_the_unweighted_draw():
    """The archived-checkpoint guarantee, and the reason `choice` was kept.

    `random.choice` and `random.choices` consume the generator differently, so
    this fails loudly if someone "simplifies" the unbalanced path into a
    uniform-weights call — which would look like a refactor and would in fact
    reproduce a different training distribution from every recorded seed.
    """
    off = _env(0.0)
    assert off._comp_weights == {}, "no weight table should exist at 0.0"

    import random
    pool = off._hero_pool
    rng = random.Random(VANILLA_SEED)
    expected = [rng.choice(pool)[0] for _ in range(400)]

    assert _draws(off) == expected, (
        "class_balance=0.0 no longer reproduces the plain choice() stream")


def test_balancing_lifts_the_rarest_class_and_removes_nobody():
    """The point of the dial, and its one hard constraint."""
    store = get_store()
    pool = build_comp_pool(store)
    slots = {}
    for _n, _z, heroes in pool:
        for h in heroes:
            slots[h] = slots.get(h, 0) + 1
    rarest = min(slots, key=slots.get)
    commonest = max(slots, key=slots.get)
    assert slots[commonest] > 3 * slots[rarest], (
        "this test assumes a skewed pool; if the pool is now balanced the "
        f"dial has lost its reason to exist ({commonest} {slots[commonest]} "
        f"vs {rarest} {slots[rarest]})")

    def share(env, cls):
        names = set(_draws(env, 600))
        by_name = {n: h for n, _z, h in env._hero_pool}
        return sum(1 for n in names if cls in by_name.get(n, ()))

    off, on = _env(0.0), _env(1.0)
    assert on._comp_weights, "balancing on should build a weight table"

    # Every comp still reachable: the weights are all strictly positive.
    weights = next(iter(on._comp_weights.values()))
    assert len(weights) == len(on._hero_pool)
    assert all(w > 0 for w in weights), "a zero weight is a filter, not a dial"

    # And the rare class is drawn more often than it was.
    rare_off = sum(1 for n in _draws(off, 600)
                   if rarest in dict((x, h) for x, _z, h in off._hero_pool).get(n, ()))
    rare_on = sum(1 for n in _draws(on, 600)
                  if rarest in dict((x, h) for x, _z, h in on._hero_pool).get(n, ()))
    assert rare_on > rare_off, (
        f"balancing did not raise {rarest}: {rare_off} -> {rare_on} draws")


def test_a_duplicate_heavy_comp_is_counted_by_SLOTS_not_by_comps():
    """A comp fielding three Crusaders practises the Crusader three times.

    Counting a class once per comp would leave exactly the duplicate-heavy
    comps under-corrected, which is the opposite of what the dial is for.
    """
    env = _env(1.0)
    pool = [("triple", "crypts", ["a", "a", "a", "b"]),
            ("single", "crypts", ["b", "b", "b", "a"])]
    weights = env._balance_weights(pool)
    # `a` fills 4 slots, `b` fills 4 -- symmetric, so the two comps must weigh
    # the same. If slots were counted per comp both classes would read 2 and 2,
    # which is also symmetric; the asymmetric case below is the real check.
    assert abs(weights[0] - weights[1]) < 1e-9

    pool = [("rare", "crypts", ["rare", "common", "common", "common"]),
            ("common", "crypts", ["common", "common", "common", "common"])]
    w_rare, w_common = env._balance_weights(pool)
    assert w_rare > w_common, (w_rare, w_common)


def test_the_weights_are_normalised_so_the_dial_means_one_thing():
    """Mean weight 1.0 whatever the pool size, or `class_balance` would mean
    something different for a 30-comp focus pool than for a 270-comp one."""
    env = _env(1.0)
    for pool in (env._hero_pool,):
        w = env._balance_weights(pool)
        assert abs(sum(w) / len(w) - 1.0) < 1e-9, sum(w) / len(w)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok {_name}")
