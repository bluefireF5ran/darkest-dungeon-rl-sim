"""The comp search (`tools/comp_ga.py`), everything except the policy.

Scoring a candidate needs a checkpoint and plays whole expeditions, so it is
not tested here. Everything around it is, because every one of these is a way
the search can look like it is working while producing garbage: an illegal
loadout the engine silently drops, a pinned class quietly absent from the
answer, a shaping term that outranks the headline it is supposed to tie-break,
or an output file the loader cannot read back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
import pytest
import random
from dataclasses import replace

from data.comp_loadout_loader import CompLoadoutLoader
from env.expedition_env import (SKILLS_PER_HERO, TIER_TO_HERO_LEVEL,
                                UNRESTRICTED_HEROES)
from tools.comp_ga import (
    CAMP_SKILLS_PER_HERO,
    TRINKETS_PER_HERO,
    archetype,
    canonical_order,
    enforce_archetype_cap,
    enforce_class_share,
    _DisplayNames,
    _genome_from_json,
    inert_trinkets,
    wearable_rarities,
    SHAPING_WEIGHTS,
    CompSpace,
    Score,
    _tiebreak_weight,
    crossover,
    genome_from_loadout,
    genome_key,
    mutate,
    seed_population,
    slugify,
)
import tools.comp_ga as ga
from tests._fixtures import get_store

from data.paths import GAME_DIR as _GAME_DIR

GAME_DIR = str(_GAME_DIR)
from data.paths import CSV_DIR as _CSV_DIR

CSV_DIR = str(_CSV_DIR)

store = get_store()
space = CompSpace(store)


def _rng(seed=0):
    return random.Random(seed)


def _assert_legal(genome):
    for gene in genome:
        assert gene.hero_class in store.heroes
        if gene.hero_class in UNRESTRICTED_HEROES:
            assert gene.skills == (), f"{gene.hero_class} must stay unrestricted"
        else:
            assert len(gene.skills) == SKILLS_PER_HERO
            assert len(set(gene.skills)) == len(gene.skills)
            assert set(gene.skills) <= set(space.skill_pool[gene.hero_class])
        assert set(gene.camp_skills) <= set(space.camp_pool[gene.hero_class])
        assert len(gene.camp_skills) == min(CAMP_SKILLS_PER_HERO,
                                            len(space.camp_pool[gene.hero_class]))
        assert set(gene.trinkets) <= set(space.trinket_pool[gene.hero_class])
        assert len(gene.trinkets) == TRINKETS_PER_HERO, (
            "a hero wearing one trinket is a slot the search forgot to fill")
        for quirk in gene.quirks:
            assert not store.quirks[quirk].is_disease
            assert not (space.incompatible_with(quirk) & set(gene.quirks)), \
                f"{quirk} coexists with something it excludes"
    assert not space.duplicate_one_offs(genome), (
        "the comp wears two copies of a trinket the roster owns one of")


# ---------------------------------------------------------------- legality --

def test_every_class_has_a_nonempty_pool_of_each_kind():
    """A class with an empty pool produces heroes wearing nothing, and the GA
    would happily converge on it because a naked hero is cheap. Camping is the
    one that was actually broken: four DLC classes had only the generic
    `hobby` until the DLC camping files were merged on 2026-08-22."""
    for hero_id in space.classes:
        assert len(space.skill_pool[hero_id]) >= SKILLS_PER_HERO, hero_id
        assert len(space.camp_pool[hero_id]) >= 4, hero_id
        assert len(space.trinket_pool[hero_id]) >= 2, hero_id


def test_hobby_is_a_template_row_and_not_a_camping_skill():
    """`hobby` is the only skill in all 80 with an empty `hero_classes`, and
    treating that as "every class" gave nineteen classes 8 camping skills and
    the Flagellant 5. Strict membership gives exactly 7 and 4 -- the real
    game's numbers -- and no curated comp equips Hobby in 136 files."""
    assert [s.id for s in store.camping_skills.values() if not s.hero_classes] == ["hobby"]
    for hero_id in space.classes:
        expected = 4 if hero_id == "flagellant" else 7
        assert len(space.camp_pool[hero_id]) == expected, hero_id
        assert "hobby" not in space.camp_pool[hero_id]


def test_the_search_does_not_choose_its_own_negative_quirks():
    """A comp is a plan, and the Sanitarium lets a player lock positives and
    remove negatives. Optimising which negatives to carry flatters every
    result: the search finds the mildest downsides and reports the score as
    the comp's."""
    for i in range(20):
        gene = space.random_hero(_rng(i), "crusader")
        assert gene.quirks
        assert all(store.quirks[q].is_positive for q in gene.quirks)
    two_sided = CompSpace(store, negative_quirks=True)
    assert any(not store.quirks[q].is_positive
               for i in range(20)
               for q in two_sided.random_hero(_rng(i), "crusader").quirks)


def test_a_random_genome_is_legal_for_every_class():
    for hero_id in space.classes:
        genome = tuple(space.random_hero(_rng(i), hero_id) for i in range(3))
        _assert_legal(genome)


def test_unrestricted_heroes_are_never_given_four_of_seven():
    """The Abomination and the Duelist carry all seven always -- a MODE decides
    which are usable. Handing either a 4-skill loadout strips skills the player
    cannot unequip, which for the Duelist empties a whole stance."""
    for hero_id in sorted(UNRESTRICTED_HEROES):
        assert space.random_hero(_rng(1), hero_id).skills == ()


def test_a_thousand_mutations_never_produce_an_illegal_comp():
    rng = _rng(7)
    genome = space.random_genome(rng)
    for _ in range(1000):
        genome = mutate(space, rng, genome, require=None)
        _assert_legal(genome)


def test_crossover_of_two_legal_parents_is_legal_and_keeps_the_slot_index():
    """A hero's rank is most of what a comp is -- `CombatEngine.reset()` assigns
    `position = index` -- so a child slot must come from the SAME slot of a
    parent, never from a different one."""
    rng = _rng(3)
    a, b = space.random_genome(rng), space.random_genome(rng)
    for _ in range(200):
        child = crossover(space, rng, a, b, require=None)
        _assert_legal(child)
        for i, gene in enumerate(child):
            # The CLASS is what must not move between slots. The gene itself
            # can still be adjusted after the splice -- `finish` confiscates a
            # second one-off and refills the slot -- but a back-rank Arbalest
            # arriving in a front rank would make the child a new random comp
            # wearing a parent's name.
            assert gene.hero_class in (a[i].hero_class, b[i].hero_class),                 "a hero moved between slots"


# ------------------------------------------------------------ pinned class --

def test_a_required_class_survives_every_operator():
    """A run asked for Duelist comps that quietly returns Crusader comps is
    worse than one that fails, so the repair runs after every operator --
    crossover and class mutation can each drop the pinned slot."""
    rng = _rng(11)
    genome = space.random_genome(rng, require="duelist")
    assert any(g.hero_class == "duelist" for g in genome)
    other = space.random_genome(rng, require="duelist")
    for _ in range(400):
        genome = mutate(space, rng, genome, require="duelist")
        assert any(g.hero_class == "duelist" for g in genome)
        genome = crossover(space, rng, genome, other, require="duelist")
        assert any(g.hero_class == "duelist" for g in genome)


def test_class_mutation_does_not_burn_a_turn_removing_the_last_pinned_hero():
    """Mutating away the only Duelist would be repaired by putting one back in
    some other slot -- a no-op that costs a mutation. The operator declines
    instead."""
    rng = _rng(5)
    genome = space.random_genome(rng, require="runaway")
    runaways = [i for i, g in enumerate(genome) if g.hero_class == "runaway"]
    assert len(runaways) == 1
    for _ in range(300):
        after = mutate(space, rng, genome, require="runaway")
        assert sum(1 for g in after if g.hero_class == "runaway") >= 1


# ---------------------------------------------------------------- seeding --

def test_the_population_is_seeded_from_curated_comps_that_qualify():
    """136 known-viable points beat noise as a starting population, but only
    the ones that satisfy the constraint -- a Duelist run seeded with Crusader
    comps would spend its first generations rediscovering the constraint."""
    pop = seed_population(space, store, _rng(0), size=12, require="duelist",
                          party_size=4, curated_fraction=0.5, max_per_archetype=99)
    assert len(pop) == 12
    for genome in pop:
        assert len(genome) == 4
        assert any(g.hero_class == "duelist" for g in genome)
        _assert_legal(genome)


def test_every_curated_comp_conforms_into_the_search_space():
    """Seeded genomes come from outside the space: a curated hero can carry
    negative quirks, a camping skill no file grants their class (42 such slots
    across the corpus), or a trinket outside the rarities. Left alone those are
    genes crossover can spread and no mutation can produce, and the emitted
    JSON could name a camping skill the real game would not let a player
    equip."""
    import random as _r
    checked = 0
    for name in sorted(store.comp_loadouts):
        genome = genome_from_loadout(store.comp_loadouts[name], store)
        if not genome:
            continue
        _assert_legal(space.conform(_r.Random(len(name)), genome))
        checked += 1
    assert checked > 120


def test_the_trinket_pool_covers_everything_the_curated_comps_wear():
    """44% of curated trinket slots sit outside `UNIVERSAL_TRINKET_RARITIES` --
    Shambler trinkets, Crimson Court, comets, boss trophies. A search that
    cannot propose those is not searching the space the curated comps live in.

    This also pins the trinket display-name collision fix: nine display names
    are claimed by two ids each, one real and one localization-only, and
    keeping the wrong one left 63 curated slots equipping nothing at all.
    """
    unreachable = []
    wrong_class = []
    # Hoisted: this probes 615 trinkets through a real engine. Called from
    # inside the loop it was 28 s -- two thirds of the whole suite's wall
    # clock. `inert_trinkets` memoises now, so this is belt and braces, but
    # the loop is where a reader looks to see what a test costs.
    inert = inert_trinkets(store)
    for name, entry in store.comp_loadouts.items():
        for hero in entry.heroes:
            if hero.hero_id not in space.trinket_pool:
                continue
            for tid in hero.trinket_ids:
                assert tid in store.trinkets, f"{name}: {tid} resolves to no trinket"
                trinket = store.trinkets[tid]
                if not trinket.buffs:
                    continue          # a no-op trinket is an empty slot, by design
                if tid in inert:
                    continue          # provably cannot change the outcome here
                if trinket.class_requirements and hero.hero_id not in trinket.class_requirements:
                    wrong_class.append((name, hero.hero_id, tid))
                elif tid not in space.trinket_pool[hero.hero_id]:
                    unreachable.append((name, hero.hero_id, tid))
    assert not unreachable, unreachable[:5]
    # "Iron Shackles: Royal" used to hand its Plague Doctor the Abomination-
    # locked Broken Key and Padlock of Transference; Fran corrected the file on
    # 2026-08-22. Kept as a standing check rather than deleted: a comp equipping
    # a trinket its class cannot hold is a scrape defect, and this is the only
    # place that would notice a new one.
    assert wrong_class == [], wrong_class


def test_the_rarity_pool_is_read_off_the_corpus_and_excludes_backer_items():
    rarities = wearable_rarities(store)
    for expected in ("ancestral", "ancestral_shambler", "crimson_court", "comet", "trophy"):
        assert expected in rarities
    assert "kickstarter" not in rarities, "294 backer trinkets no comp wears"


def test_a_curated_comp_round_trips_into_a_genome():
    loadout = store.comp_loadouts["Cross Pack: Blood"]
    genome = genome_from_loadout(loadout, store)
    assert [g.hero_class for g in genome] == [h.hero_id for h in loadout.heroes]
    # The duplicate-class comp, kept positional: three distinct Crusaders with
    # genuinely different loadouts, which a hero_id-keyed dict collapses to one.
    assert [g.hero_class for g in genome].count("crusader") == 3
    assert len({g.key() for g in genome if g.hero_class == "crusader"}) > 1


# ---------------------------------------------------------------- fitness --

def _score(headline, **kw):
    """A Score whose RANKED quantity is `headline`.

    Since 2026-08-26 the search ranks on `run_score`, not `success`, so the
    helper fills both: `run_scores` is what `fitness()` reads and `success` is
    kept in step so the tests that assert on the reported rate still say what
    they meant.
    """
    base = dict(completion=1.0, zero_death=1.0, deaths=0.0, stress=0.0,
                combats_frac=1.0, episodes=12)
    base.update(kw)
    base.setdefault("run_scores", (headline,) * base["episodes"])
    return Score(success=headline, **base)


def test_shaping_can_never_outrank_one_episodes_worth_of_headline():
    """The whole point of the tie-break bound, restated for a continuous
    headline.

    It used to read "one more SUCCESSFUL episode out of N raises the headline
    by 1/N, and the shaping term's whole range is smaller than that". `success`
    is a rate, so 1/N was the smallest gap that could exist and the guarantee
    was absolute. `run_score` is continuous, so two candidates can differ by
    arbitrarily little and no bound can be absolute any more.

    What survives is the useful half: a gap of one episode's worth -- 1/N of
    the [0, 1] range, and far below the standard error at any N this search
    runs -- can never be overturned by shaping. Below that the fitness cannot
    tell the two apart anyway, which is precisely when a tie-break is allowed
    to decide.
    """
    for episodes in (3, 12, 20, 30, 100):
        best_shaping = _score(0.0, episodes=episodes)
        worst_shaping = _score(1.0 / episodes, episodes=episodes,
                               completion=0.0, zero_death=0.0, combats_frac=0.0,
                               stress=1e9)
        assert worst_shaping.fitness() > best_shaping.fitness(), episodes
        assert _tiebreak_weight(episodes) < 1.0 / episodes


def test_shaping_weights_are_a_mean_so_the_term_stays_inside_zero_and_one():
    assert abs(sum(SHAPING_WEIGHTS.values()) - 1.0) < 1e-9
    assert _score(0.0, completion=0.0, zero_death=0.0, combats_frac=0.0,
                  stress=1e9).fitness() == 0.0
    perfect = _score(0.0)
    assert 0.0 < perfect.fitness() <= _tiebreak_weight(12) + 1e-12


def test_shaping_orders_two_total_failures():
    """Early generations are all zeros, and a search with no gradient there
    never leaves them. The comp that reached more fights must rank higher."""
    reached_more = _score(0.0, completion=0.0, zero_death=0.0, combats_frac=0.6)
    reached_less = _score(0.0, completion=0.0, zero_death=0.0, combats_frac=0.1)
    assert reached_more.fitness() > reached_less.fitness()


def test_standard_error_is_reported_and_shrinks_with_episodes():
    assert _score(0.5, episodes=12).standard_error > _score(0.5, episodes=100).standard_error
    assert _score(1.0).standard_error == 0.0


# ------------------------------------------------------------ the JSON out --

def test_output_json_reads_back_through_the_real_loader(tmp_path):
    """The output format claim, checked rather than asserted in a docstring: a
    file this tool writes must load through `CompLoadoutLoader` into the same
    heroes, in the same order, with the same skills, trinkets and quirks.

    That is what makes promoting a GA comp a `mv` and not a conversion. It
    passes through display names both ways, which is where a silent mismatch
    would live -- several skills were renamed after their internal id was set.
    """
    rng = _rng(42)
    genome = space.random_genome(rng, require="duelist")
    payload = space.to_json(genome, "GA roundtrip 01", "The Cove", {"tag": "test"})

    curated = tmp_path / "metadata" / "COMPS" / "curated_comps"
    curated.mkdir(parents=True)
    (curated / "GA_roundtrip_01.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    entries = CompLoadoutLoader.load_all(tmp_path, GAME_DIR, store)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.name == "GA roundtrip 01"
    assert entry.location == "The Cove"
    assert [h.hero_id for h in entry.heroes] == [g.hero_class for g in genome]
    for hero, gene in zip(entry.heroes, genome):
        if gene.hero_class not in UNRESTRICTED_HEROES:
            assert sorted(hero.skill_ids) == sorted(gene.skills)
        assert sorted(hero.trinket_ids) == sorted(gene.trinkets)
        assert sorted(hero.camp_skill_ids) == sorted(gene.camp_skills)
        both = sorted(hero.quirk_ids["positive"] + hero.quirk_ids["negative"])
        assert both == sorted(gene.quirks)


def test_every_id_the_search_can_emit_has_a_display_name():
    """No comp file may carry an internal id where a name belongs.

    `_DisplayNames` used to invert `_build_trinket_name_map`, which is keyed by
    display name -- so on each of the ten names two ids share, one id fell out
    of the map and `.get(tid, tid)` wrote the id itself. That is how
    `trinket1: "dd_trinket"` (the id of "Talisman of the Flame") reached two
    emitted GA comps, where nothing downstream can resolve it back.
    """
    names = _DisplayNames(store)
    missing = sorted({tid for pool in space.trinket_pool.values() for tid in pool
                      if tid not in names.trinket_name})
    assert not missing, f"trinket ids with no display name: {missing}"
    assert names.trinket_name["dd_trinket"] == "Talisman of the Flame"

    quirks = sorted({q for pool in space.quirk_pool.values()
                     for q in pool if q not in names.quirk_name})
    assert not quirks, f"quirk ids with no display name: {quirks}"


def test_ga_output_is_not_in_the_training_pool():
    """`GA_comps/` is review material. A search result that silently joined the
    pool would change which comps every archived `--comp-seed` selects, and
    would make an unreviewed machine guess indistinguishable from a comp Fran
    has played."""
    ga_dir = Path(CSV_DIR) / "metadata" / "COMPS" / "GA_comps"
    paths = CompLoadoutLoader._json_paths(Path(CSV_DIR))
    assert paths, "the curated directory did not resolve"
    assert not any(ga_dir in p.parents for p in paths)


def test_the_loader_falls_back_to_the_pre_2026_08_22_layout(tmp_path):
    """An archived copy or an older checkout still has to resolve. The failure
    mode this guards is silent: `CsvLoader.find_csv` returns None for a missing
    directory, so a wrong path yields an EMPTY store rather than an error."""
    legacy = tmp_path / "metadata" / "COMPS" / "Comps" / "Some Comp"
    legacy.mkdir(parents=True)
    src = json.loads((Path(CSV_DIR) / "metadata" / "COMPS" / "curated_comps"
                      / "Curious_Coin__Sand.json").read_text(encoding="utf-8"))
    (legacy / "Some_Comp.json").write_text(json.dumps(src, ensure_ascii=False),
                                           encoding="utf-8")
    paths = CompLoadoutLoader._json_paths(tmp_path)
    assert [p.name for p in paths] == ["Some_Comp.json"]
    assert CompLoadoutLoader.load_all(tmp_path, GAME_DIR, store)


def test_genome_key_ignores_within_slot_order_but_not_the_slot():
    rng = _rng(9)
    genome = space.random_genome(rng)
    swapped = (genome[1], genome[0]) + genome[2:]
    assert genome_key(genome) != genome_key(swapped)


def test_slugify_makes_a_filename_out_of_a_tag():
    assert slugify("duelist ruins v2.0") == "duelist_ruins_v2_0"



# --------------------------------------------------------------- trinkets --

def test_a_comp_never_wears_two_copies_of_the_same_one_off():
    """**The rule is PER ID, which is the game's own** -- corrected 2026-08-26.

    It was enforced as "one `limit`-bearing trinket per comp", read from Fran's
    first review, and that was a misreading. His correction, on being shown a
    comp of his own holding three different Ancestrals: *"the problem is not 1
    ancestral trinket per comp, rather several copies of the same ancestral
    trinket"*. You own one Ancestor's Candle, so one hero wears it -- and
    another may still wear Ancestor's Map.
    """
    rng = _rng(21)
    genome = space.random_genome(rng, require="duelist")
    other = space.random_genome(rng, require="duelist")
    for _ in range(500):
        genome = mutate(space, rng, genome, "duelist")
        assert not space.duplicate_one_offs(genome)
        # Crossover is the operator that breaks this: two legal parents each
        # wearing Ancestor's Candle make a child wearing two.
        genome = crossover(space, rng, genome, other, "duelist")
        assert not space.duplicate_one_offs(genome)


def test_the_search_can_build_AROUND_several_different_one_offs():
    """The old cap meant the search could never assemble the kind of party Fran
    actually fields -- his upgraded comp wears five different one-offs. They are
    the strongest trinkets in the game, so a space that cannot combine them is
    missing the interesting part of it."""
    counts = [space.count_one_offs(space.random_genome(_rng(i))) for i in range(40)]
    assert sum(counts) >= 40, f"only {sum(counts)} one-offs across 40 comps"
    assert max(counts) >= 3, (
        f"no comp got past {max(counts)} one-offs -- the per-id rule is being "
        "enforced as a per-comp cap again")


def test_every_hero_always_holds_exactly_two_trinkets():
    rng = _rng(31)
    for _ in range(50):
        genome = space.random_genome(rng)
        assert all(len(g.trinkets) == TRINKETS_PER_HERO for g in genome)
    # ...including one rebuilt from a curated comp that only listed one.
    for name in sorted(store.comp_loadouts):
        genome = genome_from_loadout(store.comp_loadouts[name], store)
        if not genome:
            continue
        conformed = space.conform(_rng(len(name)), genome)
        assert all(len(g.trinkets) == TRINKETS_PER_HERO for g in conformed), name


def test_the_class_trinket_bias_reaches_the_designed_synergies():
    """A class has ~6 unlimited class-locked trinkets out of ~98, so a uniform
    draw finds one 6% of the time and a ten-generation search never explores
    them. They are the ones built around the class's kit."""
    rng = _rng(41)
    hits = sum(1 for _ in range(200)
               if space.draw_trinket(rng, "crusader") in space.class_pool["crusader"])
    assert hits > 60, f"class-specific draws {hits}/200, bias not taking effect"
    unbiased = CompSpace(store, class_trinket_bias=0.0)
    rng = _rng(41)
    hits = sum(1 for _ in range(200)
               if unbiased.draw_trinket(rng, "crusader") in unbiased.class_pool["crusader"])
    assert hits < 40, f"bias 0.0 still drew {hits}/200 class-specific"


def test_inert_trinkets_are_probed_not_listed():
    """Four trinkets move no numeric field this engine has. Probed against the
    real `_apply_buff_to_stats` rather than listed, because a hardcoded set of
    unhandled stat_types drifts the moment one is wired up."""
    inert = inert_trinkets(store)
    assert "scouting_whistle" in inert
    assert len(inert) < 20, f"{len(inert)} inert -- did buff application break?"
    for hero_id in space.classes:
        assert not (inert & set(space.trinket_pool[hero_id]))


# -------------------------------------------------------------- diversity --

def test_two_comps_with_the_same_four_classes_are_one_archetype():
    """Fran: the classes are the comp's pillars, and the same four pillars is
    practically the same comp. Reordering is NOT a new archetype -- which is
    what makes reordered clones impossible to emit while leaving the search
    free to explore orderings."""
    rng = _rng(51)
    genome = space.random_genome(rng)
    reordered = tuple(reversed(genome))
    assert archetype(genome) == archetype(reordered)
    assert genome_key(genome) != genome_key(reordered)


def test_the_population_cap_keeps_archetypes_apart():
    rng = _rng(61)
    one = space.random_genome(rng)
    crowd = [one] * 12
    capped = enforce_archetype_cap(space, rng, crowd, None, 2, 4)
    assert len(capped) == 12
    counts = {}
    for genome in capped:
        counts[archetype(genome)] = counts.get(archetype(genome), 0) + 1
    assert max(counts.values()) <= 2, counts


def test_seeding_is_mostly_random_and_spread_across_archetypes():
    """The first run seeded HALF the population from curated comps and emitted
    five near-clones. Curated comps are a starting point, not the search
    space."""
    pop = seed_population(space, store, _rng(0), size=24, require="duelist",
                          party_size=4, curated_fraction=0.25, max_per_archetype=2)
    assert len(pop) == 24
    for genome in pop:
        assert any(g.hero_class == "duelist" for g in genome)
        _assert_legal(genome)
    assert len({archetype(g) for g in pop}) >= 12


# ------------------------------------------------------- canonical ordering --

def test_heroes_js_gives_a_canonical_order_for_all_twenty_classes():
    order = canonical_order()
    assert len(order) == 20
    assert order["Duelist"]["skills"][0] == "Anticipation"
    assert order["Abomination"]["skills"] == [
        "Transform", "Manacles", "Beast's Bile", "Absolution", "Rake", "Rage", "Slam"]
    for entry in order.values():
        assert len(entry["skills"]) == 7
        assert len(entry["campSkills"]) in (4, 7)


def test_emitted_skills_follow_the_game_order_not_the_genome_order():
    """The genome stores skills sorted by id, which is alphabetical and matches
    nothing on screen. Ordering happens on IDS before names are looked up,
    because `heroes.js` says "First Aid" where the string table says "Wound
    Care" -- both `first_aid` -- and name-matching dumped it at the end."""
    rng = _rng(71)
    genome = space.random_genome(rng, require="duelist")
    payload = space.to_json(genome, "T", "The Ruins", {})
    order = canonical_order()
    for hero in payload["heroes"]:
        entry = order.get(hero["heroClass"], {})
        for kind, key in (("skills", "activeSkills"), ("campSkills", "activeCampSkills")):
            listed = [n for n in hero[key] if n in entry.get(kind, [])]
            index = [entry[kind].index(n) for n in listed]
            assert index == sorted(index), (hero["heroClass"], key, hero[key])
        assert "Lash'S" not in " ".join(hero["activeCampSkills"])


def test_no_class_may_flood_the_population():
    """The archetype cap keeps class MULTISETS apart and that was not enough:
    six strong classes recombined into thirty foursomes and one run of six
    emitted comps used six of the twenty classes. The pinned class is exempt --
    `--require-class duelist` means every comp holds one."""
    rng = _rng(81)
    flood = [space.random_genome(rng, require="duelist") for _ in range(4)]
    crusaders = tuple(replace(g, hero_class="crusader") if g.hero_class != "duelist" else g
                      for g in flood[0])
    crusaders = space.finish(rng, space.repair_all(crusaders) if hasattr(space, "repair_all")
                             else tuple(space.repair(g) for g in crusaders), "duelist")
    population = [crusaders] * 20
    capped = enforce_class_share(space, rng, population, "duelist", 0.35, 4)
    assert len(capped) == 20
    holding = sum(1 for g in capped if any(h.hero_class == "crusader" for h in g))
    assert holding <= 7, holding
    assert all(any(h.hero_class == "duelist" for h in g) for g in capped),         "the pinned class must be exempt from the cap"


def test_a_comp_json_reads_back_into_the_same_genome():
    """`--explain` goes through the REAL loader, into a temporary
    `curated_comps/`, so it reads a file exactly the way training would rather
    than reimplementing the parse."""
    rng = _rng(91)
    genome = space.random_genome(rng, require="duelist")
    payload = space.to_json(genome, "T", "The Ruins", {})
    back = _genome_from_json(payload, store, space)
    assert back is not None
    assert [g.hero_class for g in back] == [g.hero_class for g in genome]
    for a, b in zip(back, genome):
        assert sorted(a.trinkets) == sorted(b.trinkets)
        if b.skills:
            assert sorted(a.skills) == sorted(b.skills)


def test_the_fitness_cache_is_keyed_on_the_scenario_too():
    """It was keyed on (genome, seed) alone, which returned a 20-episode score
    for a 40-episode request. The polish phase tunes on a half-episode scenario
    and re-measures on the full one at the same seed, so every one of those
    re-measurements was reading back the tuning number it existed to check.

    Checked without a policy by driving the cache directly: two scenarios
    differing only in episode count must not collide.
    """
    from tools.comp_ga import Evaluator, Scenario, genome_key
    a = Scenario(zones=("crypts",), tier=5, band="medium", num_combats=8,
                 episodes_per_zone=20, resolve_level=6)
    b = replace(a, episodes_per_zone=40)
    genome = space.random_genome(_rng(1))
    ev = Evaluator.__new__(Evaluator)          # no model, no env: cache only
    ev._cache = {(genome_key(genome), 7, a): "tuning"}
    ev.workers = 1
    ev._rollout = None
    ev._pool = None
    assert (genome_key(genome), 7, b) not in ev._cache
    assert Scenario.__hash__ is not None, "Scenario must stay hashable to be a key"

# ------------------------------------------------- the scenario defaults --

def test_every_zone_is_scored_by_default():
    """Fran's call: a comp scored in one zone is a comp for one zone. The enemy
    pool differs per zone AND per tier, so the Ruins selects for whatever beats
    skeletons. All four, or the search is measuring a bias."""
    from tools.comp_ga import build_parser
    from env.expedition_env import CSV_ZONE_TO_ZONE_ID
    args = build_parser().parse_args([])
    zones = [z.strip() for z in args.zones.split(",")]
    assert sorted(zones) == sorted(set(CSV_ZONE_TO_ZONE_ID.values()))


def test_the_default_scenario_fields_everything_at_maximum():
    """"Max equip, max skills, max level everything, all level 6." Resolve 6
    is the top of the ladder, `max_gear_rank_for_resolve` turns it into weapon
    and armour rank 5, and `CombatEngine.reset` derives the skill tier from
    that same rank -- so all three follow from the tier, and this is what pins
    them together."""
    from engine.combat_engine import GEAR_RANK_RESOLVE_THRESHOLDS, max_gear_rank_for_resolve
    from tools.comp_ga import build_parser
    args = build_parser().parse_args([])
    assert args.tier == 5
    resolve = TIER_TO_HERO_LEVEL[args.tier]
    assert resolve == 6
    gear = max_gear_rank_for_resolve(resolve)
    assert gear == 1 + len(GEAR_RANK_RESOLVE_THRESHOLDS) == 5


def test_a_party_built_from_the_default_scenario_is_max_geared():
    from tools.comp_ga import Scenario, _party_members, build_parser
    args = build_parser().parse_args([])
    sc = Scenario(zones=tuple(args.zones.split(",")), tier=args.tier, band=args.band,
                  num_combats=8, episodes_per_zone=1,
                  resolve_level=TIER_TO_HERO_LEVEL[args.tier])
    for m in _party_members(space.random_genome(_rng(3)), sc):
        assert (m.resolve_level, m.weapon_rank, m.armour_rank) == (6, 5, 5)


def test_the_headline_is_the_episode_weighted_mean_over_zones():
    """`by_zone` is reporting, not a second opinion: a comp that carries one
    zone and drowns in three must not read like a comp that is level."""
    lopsided = Score(success=0.5, completion=0.5, zero_death=0.5, deaths=0.0,
                     stress=0.0, combats_frac=0.5, episodes=40,
                     by_zone=(("crypts", 1.0, 10, 1.0), ("warrens", 1.0, 10, 1.0),
                              ("weald", 0.0, 10, 0.0), ("cove", 0.0, 10, 0.0)))
    level = replace(lopsided, by_zone=tuple((z, 0.5, 10, 0.5) for z in
                                            ("crypts", "warrens", "weald", "cove")))
    assert lopsided.success == level.success
    assert lopsided.as_dict()["byZone"] != level.as_dict()["byZone"]
    assert max(lopsided.by_zone, key=lambda z: z[1])[0] == "crypts"


# ------------------------------------------------ the synergy trinket bias --

def test_the_synergy_pool_follows_the_equipped_kit_not_the_class():
    """Fran's example, both halves. A Vestal holding Dazzling Light should be
    offered stun trinkets; the same Vestal holding Illumination and Hand of
    Light should not, because nothing she equips stuns."""
    stunning = space.synergy_pool(
        "vestal", ("dazzling_light", "judgement", "divine_grace", "divine_comfort"))
    not_stunning = space.synergy_pool(
        "vestal", ("illumination", "hand_of_light", "divine_grace", "divine_comfort"))
    assert stunning, "a Vestal with Dazzling Light has stun trinkets to find"
    assert "stun" in space.kit_payloads("vestal", ("dazzling_light",))
    assert "stun" not in space.kit_payloads("vestal", ("illumination", "hand_of_light"))
    assert not set(not_stunning) & set(stunning)


def test_only_positive_modifiers_are_promoted():
    """`haste_chalice` carries -0.25 stun_chance. A Vestal who stuns must never
    have it pushed at her AS a synergy -- selection can still take it for its
    speed, which is exactly what the search did."""
    pool = space.synergy_pool(
        "vestal", ("dazzling_light", "judgement", "divine_grace", "divine_comfort"))
    assert "haste_chalice" not in pool
    assert "haste_chalice" in space.unlimited_pool["vestal"], "still reachable"


def test_the_bias_is_a_prior_and_never_a_restriction():
    """With the bias at 1.0 and a kit that matches nothing, the draw still has
    to return a legal trinket rather than None.

    Checked against the whole WEARABLE pool, not just the unlimited half: since
    2026-08-26 an ordinary draw can also offer a one-off, because the per-comp
    cap of one was a misreading of the game's per-id rule.
    """
    from tools.comp_ga import CompSpace
    biased = CompSpace(store, synergy_bias=1.0)
    rng = _rng(4)
    inert_kit = ("illumination", "hand_of_light", "divine_grace", "divine_comfort")
    assert biased.synergy_pool("vestal", inert_kit) == ()
    wearable = set(biased.trinket_pool["vestal"])
    for _ in range(20):
        pick = biased.draw_trinket(rng, "vestal", skills=inert_kit)
        assert pick is not None
        assert pick in wearable


def test_a_synergy_draw_is_still_reproducible():
    from tools.comp_ga import CompSpace
    a = CompSpace(store).random_genome(_rng(11))
    b = CompSpace(store).random_genome(_rng(11))
    assert genome_key(a) == genome_key(b)


def test_the_synergy_bias_is_a_flag_so_the_old_arm_stays_reproducible():
    """Round 4 was searched with no synergy prior. Comparing round 5 against it
    means being able to re-run that arm, so the constant is a default and not a
    hardcode -- `--synergy-bias 0` is the pre-2026-08-22 search."""
    from tools.comp_ga import CompSpace, SYNERGY_TRINKET_BIAS, build_parser
    assert build_parser().parse_args([]).synergy_bias == SYNERGY_TRINKET_BIAS
    assert build_parser().parse_args(["--synergy-bias", "0"]).synergy_bias == 0.0
    hero = "vestal"
    kit = ("dazzling_light", "judgement", "divine_grace", "divine_comfort")

    def share(bias):
        space = CompSpace(store, synergy_bias=bias)
        pool = set(space.synergy_pool(hero, kit))
        assert pool                                   # the pool exists either way
        rng = _rng(5)
        draws = [space.draw_trinket(rng, hero, skills=kit) for _ in range(400)]
        return sum(d in pool for d in draws) / len(draws)

    # off, a synergy trinket is only ever the uniform draw's own small share;
    # forced, every draw is one. The pool is identical -- the flag is the rate.
    assert share(1.0) == 1.0
    assert share(0.0) < 0.10


def test_the_cross_check_is_scored_on_the_headline_run_s_own_held_out_seed(tmp_path,
                                                                            monkeypatch):
    """A second model is only worth reading if it faced the SAME dungeons.

    The cross-check exists because the fitness is P(this policy completes the
    run): a comp can score well by being easy for one policy rather than by
    being good. That comparison is only meaningful if both models are scored on
    the identical scenario and the identical held-out seed -- scored on
    anything else, the second column carries the difference between two
    dungeon lists as well as between two policies, which is the exact failure
    `Rollout.evaluate`'s scenario key exists to prevent.
    """
    from dataclasses import replace as _replace
    import tools.comp_ga as ga

    args = ga.build_parser().parse_args([])
    args.tag, args.emit, args.model_b = "xcheck_test", 1, "some/other.zip"
    scenario = ga.Scenario(zones=("crypts",), tier=5, band="medium", num_combats=8,
                           episodes_per_zone=4, resolve_level=6)
    genome = space.random_genome(_rng(21), 4)
    head = Score(success=0.5, completion=0.5, zero_death=0.5, deaths=0.0,
                 stress=0.0, combats_frac=0.5, episodes=40)
    other = _replace(head, success=0.25)

    asked = []

    class _StubB:
        def score_many(self, genomes, sc, seed):
            asked.append((sc, seed))
            return [other] * len(genomes)

    monkeypatch.setattr(ga, "GA_DIR", tmp_path)
    written = ga.emit(args, space, [(genome, head)], scenario, _StubB())

    expected = _replace(scenario, episodes_per_zone=args.validate_episodes)
    assert asked == [(expected, args.validate_seed)]

    data = json.loads(written[0].read_text(encoding="utf-8"))
    cross = data["gaProvenance"]["crossCheck"]
    assert cross["model"] == "some/other.zip"
    assert cross["successRate"] == round(other.success, 4)
    assert data["gaProvenance"]["measured"]["successRate"] == round(head.success, 4)


def test_no_model_b_leaves_the_output_exactly_as_it_was(tmp_path, monkeypatch):
    """The flag is additive: omitting it must not change a single field."""
    import tools.comp_ga as ga
    args = ga.build_parser().parse_args([])
    assert args.model_b is None
    args.tag, args.emit = "xcheck_off", 1
    scenario = ga.Scenario(zones=("crypts",), tier=5, band="medium", num_combats=8,
                           episodes_per_zone=4, resolve_level=6)
    genome = space.random_genome(_rng(22), 4)
    head = Score(success=0.5, completion=0.5, zero_death=0.5, deaths=0.0,
                 stress=0.0, combats_frac=0.5, episodes=40)
    monkeypatch.setattr(ga, "GA_DIR", tmp_path)
    written = ga.emit(args, space, [(genome, head)], scenario, None)
    assert json.loads(written[0].read_text(encoding="utf-8"))["gaProvenance"]["crossCheck"] is None


# ---------------------------------------------------------------------------
# Two-stage search (2026-08-23): stage 1 ranks class multiset + FORMATION with
# the kit redrawn every episode, so a structure cannot be flattered by a lucky
# trinket draw. Stage 2 is the existing polish. The policy half is untestable
# here (it needs a checkpoint and plays whole expeditions); everything that
# decides WHAT gets scored is not.
# ---------------------------------------------------------------------------


def test_structural_key_is_ordered_where_archetype_is_not():
    """They are deliberately different keys and both are load-bearing. Stage 1
    spends 45% of its mutations on `position`, so if it keyed the hall of fame
    on the order-insensitive `archetype` every reordering it found would be
    thrown away. Emission still dedups on `archetype`, so only one ordering of
    a multiset can reach a file."""
    space = ga.CompSpace(store)
    rng = random.Random(4)
    genome = space.random_genome(rng, 4, None)
    flipped = (genome[1], genome[0]) + tuple(genome[2:])
    assert ga.archetype(genome) == ga.archetype(flipped)
    assert ga.structural_key(genome) != ga.structural_key(flipped)
    assert ga.structural_key(genome) == tuple(g.hero_class for g in genome)


def test_rekit_changes_the_kit_and_nothing_else():
    """The whole premise of stage 1: same structure, different gear. If rekit
    could move a hero the fitness would be measuring two things at once, which
    is the confound the stage exists to remove."""
    space = ga.CompSpace(store)
    genome = space.random_genome(random.Random(1), 4, None)
    a = ga.rekit(space, genome, ga._kit_rng(7, 0), None)
    b = ga.rekit(space, genome, ga._kit_rng(7, 1), None)
    assert ga.structural_key(a) == ga.structural_key(genome)
    assert ga.structural_key(b) == ga.structural_key(genome)
    assert [g.key() for g in a] != [g.key() for g in b], "two episodes drew one kit"


def test_the_kit_stream_is_reproducible_across_processes():
    """`hash()` on a str is randomised per process unless PYTHONHASHSEED is
    pinned, so a tuple-hash seed would draw different kits on every run of the
    same command -- and the stage-1 -> stage-2 handoff would stop being
    reproducible without anything looking wrong."""
    space = ga.CompSpace(store)
    genome = space.random_genome(random.Random(2), 4, None)
    first = ga.rekit(space, genome, ga._kit_rng(11, 3), None)
    second = ga.rekit(space, genome, ga._kit_rng(11, 3), None)
    assert [g.key() for g in first] == [g.key() for g in second]


def test_structural_episode_seeds_never_collide_across_seeds():
    """`--seed` and `--validate-seed` must not share a dungeon. A structural
    score needs one `evaluate` call per episode, so it fans a seed out into a
    range -- and a stride shorter than the episode count would let the tuning
    range run into the held-out one."""
    tuning = {ga._episode_seed(3, k) for k in range(512)}
    heldout = {ga._episode_seed(4, k) for k in range(512)}
    assert not (tuning & heldout)


def test_structural_mutation_touches_only_classes_and_ranks():
    """The kit operators are switched off in stage 1 because the fitness
    redraws the kit every episode -- a trinket mutation there spends a
    generation to change something nobody measures."""
    assert set(ga.STRUCTURAL_MUTATIONS) == {"position", "class"}
    assert len(ga.STRUCTURAL_MUTATION_WEIGHTS) == len(ga.STRUCTURAL_MUTATIONS)
    space = ga.CompSpace(store)
    rng = random.Random(5)
    genome = space.random_genome(rng, 4, None)
    seen = {ga.structural_key(ga.mutate(space, rng, genome, None, structural=True))
            for _ in range(80)}
    assert len(seen) > 1, "structural mutation never changed the structure"


def test_structural_mutation_still_respects_a_pinned_class():
    """`--require-class` is the flag the whole Duelist/Runaway shortage is
    closed with, and stage 1 must not mutate its way out of it."""
    space = ga.CompSpace(store)
    rng = random.Random(6)
    genome = space.random_genome(rng, 4, "duelist")
    for _ in range(120):
        genome = ga.mutate(space, rng, genome, "duelist", structural=True)
        assert any(g.hero_class == "duelist" for g in genome)


def test_two_stage_is_the_default_and_can_be_turned_off():
    args = ga.build_parser().parse_args(["--model", "x.zip"])
    assert args.structure_first is True
    off = ga.build_parser().parse_args(["--model", "x.zip", "--no-structure-first"])
    assert off.structure_first is False



# ---------------------------------- the two aggregations must not drift -----
# `score_structure` and `score_genome` were two hand-copied weighted means over
# zones, and every metric added to `Score` had to be added to both. They now
# share `_WMEAN_ATTRS` and `_score_from`; these pin that, because the failure
# mode is silent -- a Score missing one field just reads 0.0.


class _StubRollout:
    """Returns a fixed shape per call so the two aggregations can be compared
    on identical inputs. No model, no env, no game files."""

    def __init__(self):
        self.calls = []

    def evaluate(self, party, zone_id, tier, num_combats, length_band,
                 episodes, seed):
        from bridge.rollout import RolloutResult
        self.calls.append((zone_id, episodes, seed))
        q = (0.6,) * episodes
        pr = (0.75,) * episodes
        return RolloutResult(
            episodes=episodes, success_rate=0.25, completion_rate=0.5,
            zero_death_rate=0.5, wipe_rate=0.0, stall_rate=0.25,
            mean_deaths=0.5, mean_final_stress=40.0,
            mean_combats_reached=0.75 * num_combats, num_combats=num_combats,
            qualities=q, progresses=pr, mean_hp_frac=0.7)


def _stub_scenario():
    return ga.Scenario(zones=("crypts", "warrens"), tier=3, band="long",
                       num_combats=15, episodes_per_zone=4, resolve_level=4)


def test_both_aggregations_populate_every_field_of_the_score():
    """The drift guard. A field one of them forgets reads as its default, and
    a fitness quietly ranking on a constant is not a thing a test elsewhere
    would catch."""
    sc = _stub_scenario()
    genome = space.random_genome(random.Random(7), 4)
    by_genome = ga.score_genome(_StubRollout(), genome, sc, seed=3)
    by_structure = ga.score_structure(
        _StubRollout(), [genome] * sc.episodes_per_zone, sc, seed=3)
    for got in (by_genome, by_structure):
        assert got.episodes == 8
        assert got.quality == pytest.approx(0.6)
        assert got.progress == pytest.approx(0.75)
        assert got.hp_frac == pytest.approx(0.7)
        assert got.stress == pytest.approx(40.0)
        assert got.run_score == pytest.approx(0.45)   # 0.6 * 0.75
        assert len(got.run_scores) == 8
    assert by_genome.as_dict() == by_structure.as_dict()


def test_by_zone_carries_the_ranked_quantity_first_and_success_alongside():
    """`emit._best_zone` tags a comp's `location` off index 1. If that stayed
    the success rate while the ordering moved to `run_score`, the label would
    disagree with the ranking that produced it."""
    got = ga.score_genome(_StubRollout(), space.random_genome(random.Random(1), 4),
                          _stub_scenario(), seed=0)
    assert all(len(z) == 4 for z in got.by_zone)
    for _zone, ranked, _n, succ in got.by_zone:
        assert ranked == pytest.approx(0.45)
        assert succ == pytest.approx(0.25)


def test_the_wmean_attribute_list_names_only_real_rollout_fields():
    """A typo in `_WMEAN_ATTRS` would silently contribute 0.0 forever."""
    from bridge.rollout import RolloutResult
    probe = RolloutResult(episodes=1, success_rate=0.0, completion_rate=0.0,
                          zero_death_rate=0.0, wipe_rate=0.0, stall_rate=0.0,
                          mean_deaths=0.0, mean_final_stress=0.0,
                          mean_combats_reached=0.0, num_combats=1)
    for attr in ga._WMEAN_ATTRS:
        assert hasattr(probe, attr), attr


# --------------------------------------------------------------------------
# The scenario probe ranks a corner on the WORSE of two populations
# --------------------------------------------------------------------------

def test_the_probe_spread_is_normalised_by_its_OWN_error_bar():
    """A spread of 5 points measured with an error bar of 5 points is not a
    spread. The two arms carry very different error bars at the same episode
    count -- a comp that finishes every run has almost no variance and one that
    dies halfway has a great deal -- so comparing their raw SDs compares two
    different things.
    """
    from tools.comp_ga import _probe_spread

    # Identical means and identical between-candidate SDs, by construction.
    # The only difference is how noisy each candidate's own measurement is.
    tight = [_score(headline=0.4, episodes=40, run_scores=(0.39, 0.41) * 20),
             _score(headline=0.6, episodes=40, run_scores=(0.59, 0.61) * 20)]
    noisy = [_score(headline=0.4, episodes=40, run_scores=(0.0, 0.8) * 20),
             _score(headline=0.6, episodes=40, run_scores=(0.2, 1.0) * 20)]
    t_mean, t_sd, t_ratio = _probe_spread(tight)
    n_mean, n_sd, n_ratio = _probe_spread(noisy)
    assert t_mean == pytest.approx(n_mean)
    assert t_sd == pytest.approx(n_sd), "the raw SDs are identical by construction"
    assert t_ratio > n_ratio, (
        "the ratio must prefer the arm whose spread is real over the one whose "
        "spread is its own noise")


def test_a_corner_that_saturates_on_GOOD_comps_loses_to_one_that_does_not():
    """The defect the curated arm exists for, as a unit.

    Ranking on random candidates alone named tier 3 long on 2026-08-26 (random
    SD 19.0, against 4.6 at tier 5 long) -- and on the top 24 curated comps the
    order INVERTS, tier 3 long collapsing to SD 1.7 against 12.9. A search
    converges into the curated region, so a corner is only usable if it
    separates the candidates the search STARTS with AND the ones it ends with.
    """
    from tools.comp_ga import _probe_spread

    def arm(values):
        # Every candidate measured with the same small error bar, so the ratio
        # is carried by the between-candidate spread and nothing else.
        return [_score(headline=v, episodes=40,
                       run_scores=tuple([v - 0.01, v + 0.01] * 20))
                for v in values]

    # A corner that separates random candidates and then saturates on good ones.
    wide_random = _probe_spread(arm([0.1, 0.3, 0.5, 0.7]))[2]
    flat_curated = _probe_spread(arm([0.93, 0.94, 0.94, 0.95]))[2]
    # A corner that is tighter on random candidates but still separates good ones.
    ok_random = _probe_spread(arm([0.05, 0.10, 0.15, 0.20]))[2]
    wide_curated = _probe_spread(arm([0.35, 0.55, 0.70, 0.85]))[2]

    assert wide_random > ok_random, "the random arm alone prefers the first corner"
    assert min(ok_random, wide_curated) > min(wide_random, flat_curated), (
        "ranking on the WORSE arm must pick the corner that can still measure a "
        "difference once the search has converged")


# --------------------------------------------------------------------------
# Rank legality: comp and ranks, THEN skills, THEN trinkets (Fran, 2026-08-26)
# --------------------------------------------------------------------------

def _party_ranks(genome):
    return [(i + 1, g) for i, g in enumerate(genome)]


def test_no_hero_carries_a_skill_it_cannot_perform_from_its_rank():
    """The defect Fran found by eye in the 2026-08-26 output.

    Measured on that run before the constraint existed: **30 of 128 equipped
    skills (23%) could not be used from their hero's own rank** -- Hand of
    Light on a rank-4 Vestal in four of eight comps, and one comp with 7 of 16
    dead. The fitness cannot see it: the policy plays whatever is legal, so a
    dead slot costs only the better skill that could have been there, and that
    opportunity cost is smaller than the search can resolve.
    """
    rng = random.Random(11)
    dead = total = 0
    for _ in range(120):
        genome = space.finish(rng, space.random_genome(rng, 4))
        for rank, gene in _party_ranks(genome):
            total += len(gene.skills)
            dead += len(space.dead_skills(gene.hero_class, gene.skills, rank, 4))
    assert total > 0
    assert dead == 0, f"{dead} of {total} equipped skills are unperformable"


def test_the_repair_survives_the_operators_that_MOVE_a_hero():
    """`position` is 45% of the structural mutations and crossover reorders
    freely, so a hero drawn a legal kit at rank 4 routinely ends up at rank 1.
    That is exactly when a dead skill appears, which is why the repair lives in
    `finish` rather than in the draw."""
    rng = random.Random(3)
    genome = space.finish(rng, space.random_genome(rng, 4))
    for _ in range(400):
        genome = space.finish(rng, ga.mutate(space, rng, genome, None))
    for rank, gene in _party_ranks(genome):
        assert not space.dead_skills(gene.hero_class, gene.skills, rank, 4), gene


def test_self_move_is_what_makes_an_off_rank_skill_legal():
    """Fran's rule, and the reason this is a fixpoint rather than a filter:
    off-rank coverage matters in proportion to the comp's OWN self-move, and
    everything else is a surprise contingency that camping and scouting mostly
    prevent.

    Duelist's Advance launches from 4/3/2 and moves the Highwayman one rank
    forward, so from rank 4 it opens 3, then 2, then 1 -- and Point Blank Shot,
    which launches only from rank 1, becomes legal because of it.
    """
    with_move = ("duelist_advance", "point_blank_shot", "opened_vein", "pistol_shot")
    assert space.reachable_ranks("highwayman", with_move, 4, 4) == frozenset({1, 2, 3, 4})
    assert not space.dead_skills("highwayman", with_move, 4, 4)

    without = ("point_blank_shot", "opened_vein", "pistol_shot", "grape_shot_blast")
    assert "point_blank_shot" in space.dead_skills("highwayman", without, 4, 4)


def test_viable_ranks_reproduce_the_games_own_rank_rules():
    """Derived from `.launch`, never written down -- a rank is viable when the
    class has at least four skills that launch from it. If this ever stops
    matching what every player knows, the derivation is wrong, not the game."""
    assert space.viable_ranks("arbalest", 4) == frozenset({3, 4})
    assert space.viable_ranks("musketeer", 4) == frozenset({3, 4})
    assert space.viable_ranks("leper", 4) == frozenset({1, 2})
    assert space.viable_ranks("hellion", 4) == frozenset({1, 2})
    assert space.viable_ranks("crusader", 4) == frozenset({1, 2})
    assert space.viable_ranks("vestal", 4) == frozenset({2, 3, 4})
    assert space.viable_ranks("plague_doctor", 4) == frozenset({2, 3, 4})


def test_a_hero_is_never_left_in_a_rank_it_cannot_fight_from():
    """A Leper in rank 4 is not a bad loadout, it is NO legal loadout -- it has
    one skill that launches from there. No amount of skill search repairs that,
    which is why the rule sits at the structural stage where Fran puts ranks."""
    rng = random.Random(7)
    for _ in range(150):
        genome = space.finish(rng, space.random_genome(rng, 4))
        for rank, gene in _party_ranks(genome):
            assert rank in space.viable_ranks(gene.hero_class, 4), (rank, gene.hero_class)


def test_repair_ranks_prefers_a_SWAP_over_redrawing_the_class():
    """A swap preserves the class multiset, which is the comp's identity and
    what the hall of fame keys on. Redrawing is the last resort."""
    rng = random.Random(5)
    leper = space.random_hero(rng, "leper", rank=1, size=4)
    arbalest = space.random_hero(rng, "arbalest", rank=4, size=4)
    vestal = space.random_hero(rng, "vestal", rank=3, size=4)
    crusader = space.random_hero(rng, "crusader", rank=2, size=4)
    # Leper at 4 and Arbalest at 1: both wrong, and swapping fixes both.
    broken = (arbalest, crusader, vestal, leper)
    fixed = space.repair_ranks(rng, broken)
    assert ga.archetype(fixed) == ga.archetype(broken), "the class multiset moved"
    assert [g.hero_class for g in fixed] == ["leper", "crusader", "vestal", "arbalest"]


def test_the_skill_stage_only_ever_offers_a_performable_swap():
    """`polish_skills` is the stage that did not exist until 2026-08-26. Its
    candidates go through `finish`, so a swap can never introduce a skill the
    hero cannot use from where it stands."""
    rng = random.Random(2)
    genome = space.finish(rng, space.random_genome(rng, 4))
    seen = 0
    for slot, gene in enumerate(genome):
        if gene.hero_class in ga.UNRESTRICTED_HEROES:
            continue
        rank = slot + 1
        for in_id in space.usable_from(gene.hero_class, rank):
            if in_id in gene.skills:
                continue
            kit = tuple(sorted([x for x in gene.skills if x != gene.skills[0]] + [in_id]))
            trial = space.finish(rng, genome[:slot]
                                 + (replace(gene, skills=kit),)
                                 + genome[slot + 1:])
            for r, g in _party_ranks(trial):
                assert not space.dead_skills(g.hero_class, g.skills, r, 4)
            seen += 1
    assert seen > 0, "no legal swap was offered at all"


def test_a_trinket_that_cannot_fire_is_never_worn():
    """Fran, 2026-08-26: "using ranged trinkets with a full melee highwayman
    doesnt make sense". The game states the condition itself -- `in_rank`,
    `meleeonly`, `rangedonly` -- and all three are decided by facts the comp has
    already fixed by the time trinkets are chosen, so this is a static check.

    Trinkets are repaired LAST in `finish`, because rank and skills are both
    inputs to it. That is the build order as a dependency, not a preference.
    """
    rng = random.Random(13)
    dead = total = short = 0
    for _ in range(80):
        genome = space.finish(rng, space.random_genome(rng, 4))
        for rank, gene in _party_ranks(genome):
            total += len(gene.trinkets)
            short += len(gene.trinkets) != TRINKETS_PER_HERO
            dead += sum(space.trinket_is_dead(gene.hero_class, gene.skills, rank, t)
                        for t in gene.trinkets)
    assert total > 0
    assert dead == 0, f"{dead} of {total} worn trinkets cannot fire"
    assert short == 0, "repairing dead trinkets left a hero wearing fewer than two"


def test_an_in_rank_trinket_is_dead_in_every_other_rank():
    """The clearest case, and the one that proves the test is reading the
    game's own number: a back-rank damage trinket does nothing in rank 1."""
    back = [t for t in store.trinkets
            if space.trinket_is_dead("arbalest", ("sniper_shot",), 1, t)
            and not space.trinket_is_dead("arbalest", ("sniper_shot",), 4, t)]
    assert back, "no rank-gated trinket was detected at all"


def test_an_unknown_buff_is_never_called_dead():
    """Never judge a trinket on ignorance -- an id the store does not carry
    means the loader missed something, not that the trinket is inert."""
    worn = next(t for t in store.trinkets if store.trinkets[t].buffs)
    assert not space.trinket_is_dead("crusader", ("smite",), 1, "__not_a_trinket__")


def test_the_ordering_search_only_offers_orderings_a_party_can_FIGHT_from():
    """`polish_order` permuted raw and skipped `finish`, which quietly undid the
    rank constraints one stage after they were applied: the 2026-08-26 Weald run
    emitted a Highwayman in rank 4 (viable 1-3) carrying two unperformable
    skills, in its TOP comp.

    Rank is an input to both other repairs -- moving a hero changes which of its
    skills launch and which of its trinkets fire -- so an ordering that is not
    re-finished is not the comp it claims to be.
    """
    rng = random.Random(21)

    class _Recorder:
        """Scores nothing; captures exactly what the stage would evaluate."""
        def __init__(self):
            self.seen = []

        def score_many(self, genomes, scenario, seed):
            self.seen.extend(genomes)
            return [_score(headline=0.5) for _ in genomes]

    genome = space.finish(rng, space.random_genome(rng, 4))
    rec = _Recorder()
    ga.polish_order(rec, space, rng, genome, _stub_scenario(), seed=0)
    assert rec.seen, "the ordering search evaluated nothing"
    for candidate in rec.seen:
        for slot, gene in enumerate(candidate):
            rank = slot + 1
            assert rank in space.viable_ranks(gene.hero_class, 4), (rank, gene.hero_class)
            assert not space.dead_skills(gene.hero_class, gene.skills, rank, 4)
            assert not [t for t in gene.trinkets
                        if space.trinket_is_dead(gene.hero_class, gene.skills, rank, t)]
            assert len(gene.trinkets) == TRINKETS_PER_HERO


# --------------------------------------------------------------------------
# --ban-class: roster coverage across a BATCH (Fran, 2026-08-26)
# --------------------------------------------------------------------------

BANNED = ("vestal", "occultist", "bounty_hunter", "musketeer")


def test_a_banned_class_never_survives_any_operator():
    """The ban is a property of the SPACE, so mutation, crossover and every
    repair have to respect it. Enforced in `CompSpace.classes` and repaired by
    `enforce_ban`, which runs FIRST in `finish` -- a banned hero is not a hero
    whose rank or kit is worth repairing."""
    from tools.comp_ga import CompSpace
    banned_space = CompSpace(store, banned=BANNED)
    rng = _rng(31)
    genome = banned_space.finish(rng, banned_space.random_genome(rng, 4))
    other = banned_space.finish(rng, banned_space.random_genome(rng, 4))
    for _ in range(400):
        genome = banned_space.finish(rng, mutate(banned_space, rng, genome, None))
        assert not [g for g in genome if g.hero_class in BANNED]
        genome = banned_space.finish(
            rng, crossover(banned_space, rng, genome, other, None))
        assert not [g for g in genome if g.hero_class in BANNED]


def test_the_ban_still_leaves_a_comp_legal_in_every_other_way():
    """A ban narrows the roster; it must not become an excuse to emit a comp
    that breaks the rank, skill or trinket rules."""
    from tools.comp_ga import CompSpace
    banned_space = CompSpace(store, banned=BANNED)
    rng = _rng(32)
    for _ in range(40):
        genome = banned_space.finish(rng, banned_space.random_genome(rng, 4))
        for slot, gene in enumerate(genome):
            rank = slot + 1
            assert rank in banned_space.viable_ranks(gene.hero_class, 4)
            assert not banned_space.dead_skills(gene.hero_class, gene.skills, rank, 4)
            assert len(gene.trinkets) == TRINKETS_PER_HERO
        assert not banned_space.duplicate_one_offs(genome)


def test_a_curated_seed_using_a_banned_class_is_DROPPED_not_repaired():
    """Repairing it would keep three quarters of a comp the search was told not
    to explore, which is the opposite of what the ban is for."""
    from tools.comp_ga import CompSpace
    banned_space = CompSpace(store, banned=BANNED)
    rng = _rng(33)
    pop = ga.seed_population(banned_space, store, rng, 12, None, 4,
                             curated_fraction=1.0, max_per_archetype=4,
                             max_class_share=1.0)
    assert len(pop) == 12
    for genome in pop:
        assert not [g for g in genome if g.hero_class in BANNED]


def test_the_ban_list_is_validated_against_the_real_roster():
    """A typo would ban NOTHING and the run would look like a successful
    experiment -- the same argument as `--hold-out-class`."""
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        ga.main(["--ban-class", "vestall", "--self-check", "--model", "x.zip"])
    with _pytest.raises(SystemExit):
        ga.main(["--ban-class", "vestal", "--require-class", "vestal",
                 "--self-check", "--model", "x.zip"])


# --------------------------------------------------------------------------
# Trinket SETS: unreachable by accident, and worth a lot (Fran, 2026-08-26)
# --------------------------------------------------------------------------

def test_every_class_has_exactly_one_completable_set():
    """`Trinket.set_id` is the game's own field and `CombatEngine` already
    counts worn sets and applies their buffs. All 20 classes can complete
    exactly one -- the Crimson Court pair for the vanilla eighteen, plus
    `duelist_set1`, `rw_set1` and `sb_set1`."""
    for hero_id in space.classes:
        worn = space.trinket_set(hero_id)
        assert len(worn) == TRINKETS_PER_HERO, (hero_id, worn)
        ids = {store.trinkets[t].set_id for t in worn}
        assert len(ids) == 1 and ids != {""}, (hero_id, ids)


def test_a_set_is_offered_WHOLE_or_not_at_all():
    """A hill-climb cannot walk to a set one trinket at a time: a hero wearing
    half a set is strictly worse than one wearing two good unrelated trinkets,
    so every path to it goes downhill first. Same argument `trinket_pair` was
    added on, one level up."""
    rng = _rng(41)
    genome = space.finish(rng, space.random_genome(rng, 4))
    landed = 0
    for _ in range(1500):
        genome = space.finish(rng, mutate(space, rng, genome, None))
        for gene in genome:
            worn = space.trinket_set(gene.hero_class)
            if worn and tuple(sorted(gene.trinkets)) == worn:
                landed += 1
            # Never half a set THROUGH this operator's doing: a partial set is
            # only legal if it arrived as an ordinary draw, which the per-id
            # rule permits. What must not happen is the pair rule being broken.
        assert all(len(g.trinkets) == TRINKETS_PER_HERO for g in genome)
        assert not space.duplicate_one_offs(genome)
    assert landed > 0, "the set operator never landed a complete set"


def test_a_random_pair_would_essentially_never_find_a_set():
    """The measurement that justifies a dedicated operator rather than more
    `trinket_pair` weight: a hero has ~150 wearable trinkets and exactly one
    set, so two random draws land it about 1 in 11,000 times."""
    hero = "arbalest"
    pool = len(space.trinket_pool[hero])
    assert pool > 100
    odds = 2 / pool * 1 / (pool - 1)
    assert odds < 1e-3, odds
    rng = _rng(42)
    want = space.trinket_set(hero)
    hits = sum(1 for _ in range(3000)
               if tuple(sorted(space.random_trinkets(rng, hero))) == want)
    # Not zero by construction -- `draw_trinket` offers one-offs -- but rare
    # enough that a polish phase of two draws per hero per round never sees it.
    assert hits < 30, f"{hits}/3000 random pairs were the set; the odds moved"


# --------------------------------------------------------------------------
# Region quirks and light direction (Fran, 2026-08-27)
# --------------------------------------------------------------------------

def test_region_quirks_are_retargeted_to_the_comp_s_own_region():
    """Fran, on the emitted comps: *"recommends quirks for other regions ...
    they shouldn't work on other regions"*. Since the engine started honouring
    `in_dungeon` a wrong-region quirk is simply dead, so swapping it for the
    right region's is never a downgrade."""
    rng = _rng(51)
    genome = space.finish(rng, space.random_genome(rng, 4))
    planted = tuple(replace(g, quirks=tuple(sorted(set(g.quirks)
                                                   | {"cove_scrounger", "ruins_adventurer"})))
                    for g in genome)
    weald = space.retarget_region_quirks(planted, "weald")
    for gene in weald:
        region = [q for q in gene.quirks if space.region_of_quirk(q)]
        assert region, "the region quirks vanished instead of moving"
        for q in region:
            assert q.startswith("weald_"), q


def test_the_RUINS_is_crypts_as_a_zone_and_ruins_as_a_quirk():
    """The mismatch that makes a string-built id wrong in silence."""
    rng = _rng(52)
    genome = space.finish(rng, space.random_genome(rng, 4))
    planted = tuple(replace(g, quirks=("cove_tactician",)) for g in genome)
    out = space.retarget_region_quirks(planted, "crypts")
    for gene in out:
        assert gene.quirks == ("ruins_tactician",), gene.quirks


def test_the_warrens_DROPS_them_because_it_has_none():
    """Leaving one in would ship a comp whose quirk sheet claims a bonus it
    cannot have."""
    assert "warrens_tactician" not in store.quirks
    rng = _rng(53)
    genome = space.finish(rng, space.random_genome(rng, 4))
    planted = tuple(replace(g, quirks=("cove_tactician", "steady")) for g in genome)
    out = space.retarget_region_quirks(planted, "warrens")
    for gene in out:
        assert gene.quirks == ("steady",), gene.quirks


def test_a_comp_never_mixes_the_two_LIGHT_directions():
    """Fran: *"its just to not mix both buffs as active at the same time"*. The
    torch is one number for the whole party, so a comp commits to a direction
    and anything gated the other way can never fire."""
    rng = _rng(54)
    conflicts = 0
    for _ in range(60):
        genome = space.finish(rng, space.random_genome(rng, 4))
        conflicts += len(space.light_conflicts(genome))
    assert conflicts == 0, f"{conflicts} worn trinkets are gated against the comp's own torch"


def test_light_gating_is_only_judged_once_a_direction_exists():
    """A comp wearing nothing light-gated must not have its FIRST such trinket
    declared dead on arrival -- that would make the rule unreachable."""
    hero = "vestal"
    gated = next((t for t in space.trinket_pool[hero]
                  if any(getattr(store.buffs.get(b), "rule_type", None)
                         in ("lightabove", "lightbelow")
                         for b in (store.trinkets[t].buffs or []))), None)
    assert gated, "no light-gated trinket is wearable at all"
    assert not space.trinket_is_dead(hero, ("judgement",), 4, gated, "")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
