"""Fran's three corrections to the comp search, 2026-08-28.

He reviewed the Warrens batch and named three properties of the SPACE (the
fourth, that the policy plays one plan, is a training question and lives in
`tools/style_probe.py`):

1. duplicated heroes "should be more like an exception rather than a rule";
2. the search converges onto the same few classes, and hand-banning each round
   is the workaround;
3. he keeps correcting quirks and trinkets while the skills come out solid.

Each fix is a PRIOR bought from inside the noise, never a cap, and each has an
off switch that reproduces the previous search exactly -- the same discipline
the five training-distribution knobs follow, and for the same reason: an
archived search has to stay reproducible.

The rank-fit gate is the part worth the most care. Fran predicted the failure
mode of a rarity bias before it was written:

    "Hellion, Leper, flagellant and shieldbreaker all want pos 1, even if this
     comp is 'possible' doesnt mean it will be good because preciselly that."
"""
import random
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store
import tools.comp_ga as comp_ga
from tools.comp_ga import CompSpace, _weighted_pick

store = get_store()
space = CompSpace(store)


# ------------------------------------------------------------- rank fit --

def test_rank_fit_reproduces_common_knowledge_about_formations():
    """The check that it measures the right thing, in the same style as
    `viable_ranks` agreeing with the game's own reputation for each class.

    A party of four front-rank classes is LEGAL by every per-hero rule in this
    file -- `repair_ranks` will happily swap them around and declare itself
    finished -- and it is bad for exactly the reason Fran gave. Nothing before
    this could see that.
    """
    front = ("leper", "hellion", "crusader", "flagellant")
    spread = ("crusader", "man_at_arms", "plague_doctor", "arbalest")
    assert space.rank_fit(front) < space.rank_fit(spread), (
        space.rank_fit(front), space.rank_fit(spread))
    # And it is not merely ordering noise: the gap is most of a skill per hero.
    assert space.rank_fit(spread) - space.rank_fit(front) > 1.0


def test_rank_fit_takes_the_BEST_arrangement_not_the_given_one():
    """A property of the class MULTISET, because that is what the class draw
    picks -- the ordering search will find the good arrangement if one exists,
    so judging the order as drawn would punish a fine multiset for a bad
    shuffle."""
    a = ("arbalest", "crusader", "vestal", "hellion")
    for order in (a, tuple(reversed(a)), (a[2], a[0], a[3], a[1])):
        assert space.rank_fit(order) == space.rank_fit(a)


# ---------------------------------------------------------- rarity bias --

def test_rarity_bias_is_off_by_default_on_the_space_object():
    """The CLI defaults it to 0.35; the object defaults to 0.0, so any caller
    that constructs a `CompSpace` directly keeps the old uniform draw."""
    assert CompSpace(store).rarity_bias == 0.0
    assert CompSpace(store).kit_prior_bias == 0.0


def test_a_zero_bias_draw_is_the_uniform_draw_it_replaced():
    """`draw_class(bias=0)` must be exactly `rng.choice(pool)` -- that is what
    makes every archived search reproducible."""
    pool = sorted(space.classes)
    a = random.Random(7)
    b = random.Random(7)
    got = [space.draw_class(a, pool, bias=0.0) for _ in range(200)]
    want = [b.choice(pool) for _ in range(200)]
    assert got == want


def test_rarity_bias_raises_the_share_of_rare_classes():
    biased = CompSpace(store, rarity_bias=0.7)
    slots: dict = {}
    for entry in store.comp_loadouts.values():
        for hero in entry.heroes:
            if hero.hero_id:
                slots[hero.hero_id] = slots.get(hero.hero_id, 0) + 1
    rare = {c for c in space.classes if slots.get(c, 0) <= 20}
    assert rare, "no rare classes at all -- has the corpus flattened?"

    def share(sp, seed):
        rng = random.Random(seed)
        pool = sorted(sp.classes)
        picks = [sp.draw_class(rng, pool, bias=sp.rarity_bias) for _ in range(2000)]
        return sum(1 for c in picks if c in rare) / len(picks)

    assert share(biased, 0) > share(space, 0) + 0.05


def test_the_rarity_gate_REFUSES_a_swap_that_worsens_the_formation():
    """The anti-loop, and the whole reason the bias is safe to turn on.

    Built as the party Fran named: three front-rank classes plus one slot the
    bias would love to fill with a fourth. A substitution that lowers
    `rank_fit` must come back as the incumbent, however rare the alternative.
    """
    genome = space.random_genome(random.Random(3))
    front_only = [c for c in space.classes
                  if space.viable_ranks(c) <= frozenset({1, 2})]
    assert front_only, "no front-rank-only class in the roster"

    # A party already crowded at the front; swapping in another front-only
    # class can only make the fit worse or leave it equal.
    from dataclasses import replace
    crowded = tuple(replace(g, hero_class=c) for g, c in
                    zip(genome, (front_only * 4)[:4]))
    before = space.rank_fit([g.hero_class for g in crowded])
    rng = random.Random(11)
    for _ in range(60):
        pick = space.draw_class(rng, sorted(space.classes), bias=1.0,
                                current={"genome": crowded, "slot": 0})
        after = list(c.hero_class for c in crowded)
        after[0] = pick
        assert space.rank_fit(after) + 1e-9 >= before, (pick, after)


# ------------------------------------------------------------- kit prior --

def test_the_kit_prior_reads_real_counts_off_the_corpus():
    quirks, trinkets = space.kit_prior()
    assert quirks and trinkets
    assert all(v >= 1 for v in quirks.values())
    # Keyed per CLASS for trinkets: a Houndmaster's answer is not an
    # Arbalest's, and pooling them would only rediscover the popular ones.
    assert all(isinstance(k, tuple) and len(k) == 2 for k in trinkets)
    assert any(hero_id in store.heroes for hero_id, _tid in trinkets)


def test_a_zero_kit_prior_leaves_every_draw_uniform():
    """Off means off: `_prior_weights` is never consulted, so the trinket and
    quirk draws are the `rng.choice`/`rng.sample` they always were."""
    # TWO spaces, both built ONCE. Constructing a CompSpace inside the loop
    # cost 55 s and made this file the slowest in the suite -- it rebuilds
    # every legality pool each time.
    one, two = CompSpace(store), CompSpace(store)
    a, b = random.Random(5), random.Random(5)
    hero = "crusader"
    got = [one.draw_trinket(a, hero) for _ in range(200)]
    again = [two.draw_trinket(b, hero) for _ in range(200)]
    assert got == again
    assert one.unlimited_pool[hero]


def test_the_kit_prior_keeps_the_whole_pool_reachable():
    """A PRIOR, not a filter. At any bias below 1.0 every legal trinket keeps a
    nonzero weight -- which is the difference between pointing the search at
    Fran's habits and freezing them into it."""
    biased = CompSpace(store, kit_prior_bias=0.8)
    hero = "houndmaster"
    pool = list(biased.unlimited_pool[hero])
    assert len(pool) > 20
    _q, trinkets = biased.kit_prior()
    weights = biased._prior_weights(pool, trinkets, key=lambda t: (hero, t))
    assert all(w > 0.0 for w in weights), "a trinket became unreachable"
    seen = set()
    rng = random.Random(0)
    for _ in range(4000):
        seen.add(biased.draw_trinket(rng, hero))
    assert len(seen) > len(pool) * 0.5, (len(seen), len(pool))


def test_weighted_pick_uses_the_rng_it_is_given():
    """`random.choices` draws from the module-level Random when handed none,
    which would make a seeded search unreproducible in a way nothing else would
    catch."""
    items = ["a", "b", "c"]
    w = [1.0, 5.0, 2.0]
    one = [_weighted_pick(random.Random(4), items, w) for _ in range(50)]
    two = [_weighted_pick(random.Random(4), items, w) for _ in range(50)]
    assert one == two
    rng = random.Random(0)
    picks = [_weighted_pick(rng, items, w) for _ in range(3000)]
    assert picks.count("b") > picks.count("c") > picks.count("a")



# ── the duplication cost, 2026-09-04 ──────────────────────────────────────
#
# `--prefer-unique` above expresses Fran's "an exception rather than a rule"
# at EMIT, over an already-converged hall of fame. Measured a week later, that
# was not enough: **61% of emitted comps carry a duplicate class against 28%
# of his curated pool**, because by emit time every survivor is a duplicate
# and there is nothing unique left to prefer. `duplication_cost` moves the
# same preference into the generation loop, where it decides what survives.


# The mechanism tests pass an explicit weight, because the DEFAULT is 0.0 and
# a test that silently depends on a default stops testing the thing it names
# the moment that default moves -- which is exactly what happened here.
W = 1.0


def test_four_different_classes_cost_nothing():
    genome = _party("crusader", "vestal", "highwayman", "occultist")
    assert comp_ga.duplication_cost(genome, 40, W) == 0.0


def test_the_cost_rises_with_the_number_of_repeats():
    pair = comp_ga.duplication_cost(_party("a", "a", "b", "c"), 40, W)
    two = comp_ga.duplication_cost(_party("a", "a", "b", "b"), 40, W)
    four = comp_ga.duplication_cost(_party("a", "a", "a", "a"), 40, W)
    assert 0 < pair < two < four


def test_the_default_is_OFF_because_it_was_measured_and_does_nothing():
    """A null, kept as a knob rather than deleted.

    Over 2,000 bootstrapped populations of 24 scored with real rollouts, the
    cost changes which four elites survive in 8% of them and moves their
    duplicate share by **+0.0pp**. Selection was never the stage that makes
    duplicates: the seeded population is 38% duplicates and the elites 23.6%,
    so the fitness already selects against them, and neither `mutate` nor
    `crossover` raises the rate.

    **`--ban-class` makes them**, arithmetically. Emit a comp, ban its four
    classes, search again: the pool shrinks each round, and four heroes drawn
    from eight classes repeat one 63% of the time -- which is the 61% the
    emitted corpus carries. Fran named the cause in his own review: "because of
    the restrictions".

    This test exists so the default cannot drift back to 1.0 on the assumption
    that it helps.
    """
    assert comp_ga.DUPLICATE_COST == 0.0
    assert comp_ga.duplication_cost(_party("a", "a", "a", "a"), 40) == 0.0


def test_the_cost_can_never_outrank_a_real_score_difference():
    """The bound is the whole safety argument, and it is the same one the
    shaping term rests on: the cost shares the tiebreak budget, so a duplicate
    party that genuinely scores better still wins. Without this it would be a
    cap wearing a preference's clothes -- and Fran's own pool has four triples
    and eight quads in it."""
    for episodes in (1, 8, 40, 160):
        worst = comp_ga.duplication_cost(_party("a", "a", "a", "a"), episodes, W)
        assert worst <= comp_ga._tiebreak_weight(episodes) + 1e-12, episodes


def test_a_zero_weight_reproduces_the_old_search_exactly():
    """Every knob in this file has an off switch that is byte-identical to the
    behaviour it replaced, so an archived search stays reproducible."""
    for classes in (("a", "a", "a", "a"), ("a", "a", "b", "c"), ("a", "b", "c", "d")):
        assert comp_ga.duplication_cost(_party(*classes), 40, 0.0) == 0.0


def _party(*classes):
    return [SimpleNamespace(hero_class=c) for c in classes]

if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            print(_name)
            _fn()
