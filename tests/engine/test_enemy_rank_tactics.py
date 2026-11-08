"""The rank tactics an expert plays, as assertions about the engine.

Every claim below is quoted from the per-enemy strategy notes on the community
reference `pizzarat105.github.io/PizzaRatReview`, and every one was checked
against the monster's own `.info.darkest` before it was written down here. They
are all the same shape: **move this enemy to that rank and it loses its real
skill**, which is the disruption the guides treat as basic play.

**Why this file exists.** Until 2026-09-19 every assertion in it was false.
`_enemy_skill_weights` never read `.launch`, so a monster could use any skill
from any rank: 228 of 694 enemy skills were launch-restricted in the data and
none of the restrictions did anything. Nothing went red, because nothing
tested it. See `docs/changelog/2026-09.md`.

**These are engine tests, not a ruler.** They assert the simulator reproduces
the GAME's own rank restrictions -- adjudicated against the game files, with
the guide supplying only the list of what was worth checking. The policy is
free to learn from them; that is the point.

The tier used is the Apprentice (`_A`) variant throughout, because `.launch`
is identical across A/B/C for all of these -- the tiers differ in accuracy,
damage and crit, not in rank restrictions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

store = get_store()


def usable_at(enemy_id, rank):
    """The skill ids the engine will actually offer `enemy_id` in `rank`.

    Reads `_enemy_skill_weights`, which is the real selection path, rather
    than re-deriving the answer from `.launch` -- a test that recomputed the
    filter would pass even if the filter were never called.
    """
    engine = CombatEngine(store, SeededRNG(1))
    crusader = store.heroes["crusader"]
    ed = store.enemies[enemy_id]
    engine.reset(hero_defs=[crusader, crusader], enemy_defs=[ed],
                 hero_positions=[1, 2])
    enemy = engine.enemies[0]
    enemy.position = rank - 1
    brain = engine._get_monster_brain(ed)
    usable, _ = engine._enemy_skill_weights(enemy, ed, brain)
    return {sk_id for sk_id, _ in (usable or [])}


def targets_of(enemy_id, skill_id):
    ed = store.enemies[enemy_id]
    sk = next(s for s in ed.skills if s.id == skill_id)
    return sorted(sk.levels[max(sk.levels.keys())].target or [])


def test_bone_arbalest_is_shut_down_in_the_front_two_ranks():
    """*"It can easily be shut down by moving it into the front 2 ranks,
    where it can only use Bayonet Jab."*

    `crossbow_shot` is `.launch 43`, `bayonet_jab` is `.launch 12`."""
    for rank in (1, 2):
        assert usable_at("skeleton_arbalist_A", rank) == {"bayonet_jab"}, rank
    for rank in (3, 4):
        assert usable_at("skeleton_arbalist_A", rank) == {"crossbow_shot"}, rank
    print("  PASS test_bone_arbalest_is_shut_down_in_the_front_two_ranks")


def test_the_arbalests_crossbow_cannot_reach_hero_rank_one():
    """*"The fact that Quarrel cannot hit position 1 can be taken advantage
    of to ensure a hero's safety."*

    A targeting claim rather than a launch one, and the reason rank 1 is the
    safe rank against a backline Ruins comp."""
    assert targets_of("skeleton_arbalist_A", "crossbow_shot") == [2, 3, 4]
    print("  PASS test_the_arbalests_crossbow_cannot_reach_hero_rank_one")


def test_bone_courtier_loses_its_stress_attack_in_rank_one():
    """*"It can be shut down by moving it into position 1, where it can only
    use its weak melee skill."*

    `tempting_goblet` is `.launch 432` and carries `Stress 2`; `dagger_jab`
    is `.launch 21`. The single clearest case of disruption in the game, and
    the one that exposed the missing filter."""
    assert usable_at("skeleton_courtier_A", 1) == {"dagger_jab"}
    assert "tempting_goblet" in usable_at("skeleton_courtier_A", 2)
    print("  PASS test_bone_courtier_loses_its_stress_attack_in_rank_one")


def test_pelagic_shaman_loses_all_three_back_skills_in_rank_one():
    """*"It can be shut down by being moved to position 1, where it can only
    use its weak Melee attack."*

    The other stress caster disruption is named for. Three of its four skills
    -- `fish_stresscast`, `call_of_the_deep` and `shaman_heal` -- are
    `.launch 432`; only `shaman_stab` is `.launch 1`.

    Note the reference's own rank ICON for `call_of_the_deep` is wrong (it
    draws 1-3 against the file's `.launch 432`); `tools/pizzarat_oracle.py`
    carries that in `KNOWN_SITE_ERRORS`. Its prose is right even though its
    icon is not, which is why the game files adjudicate and not the site."""
    assert usable_at("fishman_shaman_A", 1) == {"shaman_stab"}
    back = usable_at("fishman_shaman_A", 3)
    assert {"fish_stresscast", "call_of_the_deep", "shaman_heal"} <= back
    assert "shaman_stab" not in back
    print("  PASS test_pelagic_shaman_loses_all_three_back_skills_in_rank_one")


def test_brigand_fusilier_loses_its_aoe_in_rank_one():
    """*"They can be shut down if moved to position 1, where they can only
    use Rushed Shot."*

    `blunderbuss_shot` is the AoE and is `.launch 432`; `rushed_shot` is
    `.launch 1`."""
    assert usable_at("brigand_fusilier_A", 1) == {"rushed_shot"}
    assert "blunderbuss_shot" in usable_at("brigand_fusilier_A", 2)
    print("  PASS test_brigand_fusilier_loses_its_aoe_in_rank_one")


def test_swine_skiver_is_a_non_issue_pulled_to_the_front():
    """*"try pulling it to the front ranks where it will be a non-issue for a
    turn"*

    `spit_to_roast` and the AoE `cripple_them` are both `.launch 432`; only
    `goring_flight` reaches the front."""
    assert usable_at("swine_skiver_A", 1) == {"goring_flight"}
    assert {"spit_to_roast", "cripple_them"} <= usable_at("swine_skiver_A", 3)
    print("  PASS test_swine_skiver_is_a_non_issue_pulled_to_the_front")


def test_bone_spearman_only_spams_impale_from_the_front_two():
    """*"Impale is an AoE damage move that can be spammed by the Spearman if
    it's in the front 2 positions."*

    `impale` is `.launch 12` and AoE; `spear_thrust` launches from anywhere,
    so the Spearman is never harmless -- only ever less dangerous."""
    for rank in (1, 2):
        assert "impale" in usable_at("skeleton_spear_A", rank), rank
    for rank in (3, 4):
        got = usable_at("skeleton_spear_A", rank)
        assert "impale" not in got, rank
        assert got == {"spear_thrust"}, rank
    print("  PASS test_bone_spearman_only_spams_impale_from_the_front_two")


def test_pelagic_grouper_targets_the_row_it_stands_in():
    """*"It targets the backline if in the back and the front 2 positions if
    in the front."*

    A coupled claim -- the launch restriction decides which target set is
    reachable at all. `fish_cutlass` is `.launch 12 .target 12`,
    `fish_harpoon` is `.launch 34 .target 34`."""
    assert usable_at("fishman_harpoon_A", 1) == {"fish_cutlass"}
    assert usable_at("fishman_harpoon_A", 4) == {"fish_harpoon"}
    assert targets_of("fishman_harpoon_A", "fish_cutlass") == [1, 2]
    assert targets_of("fishman_harpoon_A", "fish_harpoon") == [3, 4]
    print("  PASS test_pelagic_grouper_targets_the_row_it_stands_in")


def test_carrion_eaters_can_never_reach_hero_rank_four():
    """*"mind how Munch cannot target position 4"* and, for the big one,
    *"cannot target position 4"*.

    Both are pure targeting claims, so they held even before `.launch` was
    enforced -- included so the pair cannot regress unnoticed together."""
    assert targets_of("carrion_eater_A", "munch") == [1, 2, 3]
    for skill in ("weaken_prey", "tentacle_devour"):
        assert targets_of("carrion_eater_big_A", skill) == [1, 2, 3], skill
    print("  PASS test_carrion_eaters_can_never_reach_hero_rank_four")



# -- `.move`: the other half of a rank tactic ------------------------------
#
# `.launch` says where a monster may act FROM. `.move` says where it goes
# afterwards, and was parsed for heroes and hardcoded to 0 for monsters until
# 2026-09-19. Together they are what makes disruption temporary rather than
# permanent, which is how the game actually plays.


def test_the_fusilier_walks_back_out_of_the_rank_that_shuts_it_down():
    """Both halves, in one sequence, and neither worked before 2026-09-19.

    `rushed_shot` is `.launch 1 .move 1 0`: rank 1 is the only place the
    Fusilier can fire it from, and firing it puts it back in rank 2, where
    `blunderbuss_shot` (`.launch 432`, an AoE) is available again.

    So "shut it down by moving it to position 1" buys ONE turn, not the
    fight. With only `.launch` enforced it would have been permanent, which
    is a different game.
    """
    engine = CombatEngine(store, SeededRNG(5))
    crusader = store.heroes["crusader"]
    fusilier = store.enemies["brigand_fusilier_A"]
    mate = store.enemies["cultist_brawler_A"]
    engine.reset(hero_defs=[crusader] * 4, enemy_defs=[fusilier, mate],
                 hero_positions=[1, 2, 3, 4])
    enemy = next(e for e in engine.enemies
                 if engine._get_enemy_def(e.id).id == "brigand_fusilier_A")
    other = next(e for e in engine.enemies if e is not enemy)
    enemy.position, other.position = 0, 1

    ed = engine._get_enemy_def(enemy.id)
    brain = engine._get_monster_brain(ed)
    sk = next(s for s in ed.skills if s.id == "rushed_shot")
    sd = sk.levels[max(sk.levels.keys())]

    usable, _ = engine._enemy_skill_weights(enemy, ed, brain)
    assert {i for i, _ in usable} == {"rushed_shot"}, "rank 1 bars the AoE"

    engine._finish_enemy_action(enemy, "rushed_shot", sd, brain)
    assert enemy.position == 1, f"`.move 1 0` must retreat it, got {enemy.position}"
    assert other.position == 0, "the companion it passed takes rank 1"

    usable, _ = engine._enemy_skill_weights(enemy, ed, brain)
    assert "blunderbuss_shot" in {i for i, _ in usable}, "the AoE comes back"
    print("  PASS test_the_fusilier_walks_back_out_of_the_rank_that_shuts_it_down")


def test_a_lone_monster_cannot_self_move():
    """Fran's rule, in `_formation.py`: *"a single enemy can't be pushed as
    there isn't enough positions to rotate, a displacement always keeps the
    gaps"*. `.move` is a displacement like any other, so the last monster
    standing stays where it is however often it uses a retreating skill.

    This is why the Necromancer's retreat depends on his summons rather than
    being a property of the skill -- see the next test."""
    engine = CombatEngine(store, SeededRNG(5))
    crusader = store.heroes["crusader"]
    ed = store.enemies["brigand_fusilier_A"]
    engine.reset(hero_defs=[crusader] * 4, enemy_defs=[ed],
                 hero_positions=[1, 2, 3, 4])
    enemy = engine.enemies[0]
    enemy.position = 0
    brain = engine._get_monster_brain(ed)
    sk = next(s for s in ed.skills if s.id == "rushed_shot")
    sd = sk.levels[max(sk.levels.keys())]
    for _ in range(3):
        engine._finish_enemy_action(enemy, "rushed_shot", sd, brain)
    assert enemy.position == 0, "nothing to rotate with -- it must stay put"
    print("  PASS test_a_lone_monster_cannot_self_move")


def test_the_necromancer_retreats_behind_what_he_summons():
    """*"The Necromancer starts at rank 1, and moves back with every action he
    takes. He relies on his summons to keep him safe."*

    All three of his skills are `.move 1 0`, and all three carry
    `NecroSummon 1` -- which is the mechanism, not a coincidence: a lone
    monster cannot displace, so the summons are what give him something to
    rotate behind. The guide describes the summons and the retreat as one
    strategy and the game data agrees."""
    ed = store.enemies["necromancer_A"]
    for sk in ed.skills:
        sd = sk.levels[max(sk.levels.keys())]
        assert sd.move_self == 1, f"{sk.id} should retreat one rank"
        assert any("NecroSummon" in e for e in sd.effects), sk.id

    engine = CombatEngine(store, SeededRNG(3))
    crusader = store.heroes["crusader"]
    engine.reset(hero_defs=[crusader] * 4, enemy_defs=[ed],
                 hero_positions=[1, 2, 3, 4])
    enemy = engine.enemies[0]
    start = enemy.position
    for _ in range(4):
        engine._execute_enemy_turn(enemy)
    assert enemy.position > start, (
        f"he summons and should end up behind them, went {start} -> "
        f"{enemy.position}")
    assert enemy.position <= 3, "and never off the back of the board"
    print(f"  PASS test_the_necromancer_retreats_behind_what_he_summons "
          f"(rank {start + 1} -> {enemy.position + 1})")



def test_the_flesh_parts_only_reach_the_row_they_stand_in():
    """*"'Maws of Life' only hitting the front 2 ranks and 'Undulating
    Invasion' only being able to target the back positions."*

    The Flesh is the `formless_*` family -- the site names skills by their
    display names, and the game's string table maps "Maws of Life" to
    `formless_melee_attack` and "Undulating Invasion" to
    `formless_ranged_attack`.

    Both are `.launch` and `.target` on the SAME pair of ranks, so a part in
    the wrong row cannot act at all. That is not an edge case to paper over:
    it is why the boss carries `shape_shifter: .monster_class_valid_ranks`
    (the guide's "heads must be at front, butts at back"), and those parts are
    among the 21 (enemy, rank) pairs the launch filter legitimately strands.

    **The shared HP pool this boss also carries is NOT modelled** -- see
    `ASSUMPTIONS.md` under `shared_health`. These two claims are about reach
    and hold independently of it."""
    assert usable_at("formless_melee_A", 1) == {"formless_melee_attack"}
    assert usable_at("formless_melee_A", 2) == {"formless_melee_attack"}
    assert usable_at("formless_melee_A", 4) == set(), "no skill launches from the back"
    assert targets_of("formless_melee_A", "formless_melee_attack") == [1, 2]

    assert usable_at("formless_ranged_A", 3) == {"formless_ranged_attack"}
    assert usable_at("formless_ranged_A", 1) == set(), "no skill launches from the front"
    assert targets_of("formless_ranged_A", "formless_ranged_attack") == [3, 4]
    print("  PASS test_the_flesh_parts_only_reach_the_row_they_stand_in")


if __name__ == "__main__":
    print("=== Enemy Rank Tactics ===\n")
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
            except Exception as e:
                import traceback
                print(f"  FAIL {name}: {e}")
                traceback.print_exc()
    print("Done.")
