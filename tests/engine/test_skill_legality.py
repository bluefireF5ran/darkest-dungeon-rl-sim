"""Three `.darkest` keys that decide what is LEGAL, and were all unread.

Found by the third audit sweep (2026-08-21): diff every `.key` in every hero
`.info.darkest` against every attribute access and string literal in the
source. Only four `combat_skill` keys came back unread, and three of them
change which actions exist:

    .self_target_valid          55 skills   who may be aimed at
    .required_performer_hp_range 2 skills   when the skill exists at all
    .ignore_guard                3 skills   whether a guardian may intercept

(The fourth, `.generation_guaranteed`, is quest generation and out of scope.)

None of these is a damage number, which is why none of them ever showed up in
an eval. They are legality, and an illegal action offered to a policy is a line
it learns and the real game will not let it play.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG


@pytest.fixture(scope="module")
def store():
    return get_store()


def _skill(store, hero_id, skill_id):
    sk = next(s for s in store.heroes[hero_id].skills if s.id == skill_id)
    return sk.levels[max(sk.levels)]


def _engine(store, hero_ids, seed=1):
    engine = CombatEngine(store, SeededRNG(seed))
    engine.reset(hero_defs=[store.heroes[h] for h in hero_ids],
                 enemy_defs=[store.enemies["cultist_brawler_A"]] * 2,
                 surprise=(False, False), auto_resolve_to_hero=False)
    return engine


def _targets(engine, hero, skill_id):
    return sorted(a.target_id for a in engine.get_valid_actions(hero.id)
                  if a.skill_id == skill_id)


# ── .self_target_valid ──

def test_the_flag_is_parsed_where_the_data_says(store):
    assert _skill(store, "man_at_arms", "defender").self_target_valid is False
    assert _skill(store, "houndmaster", "guard_dog").self_target_valid is False
    assert _skill(store, "antiquarian", "protect_me").self_target_valid is False
    # ...and defaults True for an ally skill that does not say otherwise.
    assert _skill(store, "crusader", "battle_heal").self_target_valid is True


def test_a_guard_skill_cannot_guard_its_own_caster(store):
    """The three guard skills in the game all carry the flag, and guarding
    yourself is exactly the nonsense it exists to forbid: `Clear Guard
    Performer` then `MAA Guard 1` onto the performer is a hero who guards
    himself and redirects nothing."""
    engine = _engine(store, ["man_at_arms", "crusader", "vestal", "hellion"])
    maa = engine.heroes[0]
    targets = _targets(engine, maa, "defender")
    assert targets, "premise: Defender is usable at all"
    assert maa.position not in targets, targets
    # every OTHER living ally is still fair game
    assert set(targets) == {h.position for h in engine.heroes[1:]}


def test_an_ordinary_ally_skill_still_reaches_the_caster(store):
    """The flag is not "ally skills exclude self" -- `@` in `.target` is what
    puts the performer's rank in the pool, and 16 of the 23 hero ally skills
    keep it."""
    engine = _engine(store, ["crusader", "vestal", "hellion"])
    crusader = engine.heroes[0]
    crusader.hp = 1
    assert crusader.position in _targets(engine, crusader, "battle_heal")


def test_the_flagellant_cannot_take_his_own_suffering(store):
    """All four of his ally skills carry the flag, and the data says why: each
    pairs an effect on the TARGET with a self-effect on him. `endure` is
    `Flag Steal Stress 5` + `Flag Self Stress 5` -- aimed at himself it would
    be stealing his own stress."""
    engine = _engine(store, ["flagellant", "crusader", "vestal"])
    flag = engine.heroes[0]
    for sid in ("reclaim", "endure", "suffer"):
        targets = _targets(engine, flag, sid)
        assert flag.position not in targets, (sid, targets)


def test_every_flagged_skill_is_an_ally_skill_with_an_at_prefix(store):
    """The reasoning above only holds because the two markers co-occur. If a
    patch ever puts `.self_target_valid false` on a skill WITHOUT `@`, the
    performer was never in the pool and this file needs rereading."""
    flagged = [(h.id, s.id) for h in store.heroes.values() for s in h.skills
               for lv in [s.levels[max(s.levels)]] if not lv.self_target_valid]
    assert flagged, "premise: some hero skills carry it"
    for hero_id, skill_id in flagged:
        assert CombatEngine(store, SeededRNG(0))._is_ally_skill(skill_id), \
            (hero_id, skill_id)


# ── .required_performer_hp_range ──

def test_redeem_and_exsanguinate_are_gated_below_40_percent(store):
    """The two skills in the game that carry the key, both the Flagellant's.
    Unread until now, so the policy could open with either at full health --
    a line the real game does not offer, and one that inverts the class: his
    payoff skills are what he EARNS by bleeding."""
    for sid in ("redeem", "exsanguinate"):
        assert _skill(store, "flagellant", sid).required_performer_hp_range == (0.0, 0.4)

    engine = _engine(store, ["flagellant", "crusader", "vestal"])
    flag = engine.heroes[0]

    flag.hp = flag.max_hp
    offered = {a.skill_id for a in engine.get_valid_actions(flag.id)}
    assert "redeem" not in offered and "exsanguinate" not in offered, offered

    flag.hp = max(1, int(flag.max_hp * 0.3))
    offered = {a.skill_id for a in engine.get_valid_actions(flag.id)}
    assert "exsanguinate" in offered, offered


def test_the_gate_is_a_fraction_not_a_flat_hp(store):
    """`0 0.40` against max HP, so it moves with armour rank. A flat reading
    would make the skill permanently unusable on a geared Flagellant."""
    engine = _engine(store, ["flagellant", "crusader"])
    flag = engine.heroes[0]
    sd = _skill(store, "flagellant", "exsanguinate")
    flag.max_hp = 100
    flag.hp = 39
    assert engine._performer_hp_allows(flag, sd)
    flag.hp = 41
    assert not engine._performer_hp_allows(flag, sd)


def test_an_ungated_skill_is_always_offered(store):
    engine = _engine(store, ["crusader", "vestal"])
    sd = _skill(store, "crusader", "smite")
    assert sd.required_performer_hp_range is None
    hero = engine.heroes[0]
    for hp in (1, hero.max_hp // 2, hero.max_hp):
        hero.hp = hp
        assert engine._performer_hp_allows(hero, sd)


# ── .ignore_guard ──

def test_three_skills_ignore_guard_and_one_is_named_for_it(store):
    """Worth pinning as a list rather than a count, because the membership is
    the evidence that the flag means what it says: the **Shieldbreaker's
    `break_guard`** is named for the mechanic and does nothing else.

    A first pass at this grepped only `heroes/` and reported one skill, the
    Jester's -- both other carriers are DLC classes living under `dlc/`, which
    is exactly the directory `HeroLoader`'s recursive glob exists to reach."""
    flagged = sorted((h.id, s.id) for h in store.heroes.values() for s in h.skills
                     for lv in [s.levels[max(s.levels)]] if lv.ignore_guard)
    assert flagged == [("jester", "dirk_stab"),
                       ("runaway", "backdraft"),
                       ("shieldbreaker", "break_guard")], flagged


def test_a_guardian_does_not_intercept_it(store):
    """End to end, and with a control on the same board.

    An enemy guards its neighbour; the Jester attacks the guarded one. With
    `dirk_stab` the guardian must never take the damage, and with an ordinary
    Jester attack it sometimes must — otherwise this would pass equally well
    if the guard machinery had simply stopped running.
    """
    from engine.action import Action

    def run(skill_id, seed):
        engine = _engine(store, ["jester", "crusader"], seed=seed)
        aimed, guardian = engine.enemies[0], engine.enemies[1]
        aimed.dodge = 0.0
        engine._resolve_effect(aimed, guardian, store.effects["Guard 1"], None)
        assert aimed.guard_source_id == guardian.id, "premise: the guard landed"
        hp = (aimed.hp, guardian.hp)
        jester = engine.heroes[0]
        engine.submit_action(Action(jester.id, skill_id, aimed.position))
        engine.step()
        return aimed.hp < hp[0], guardian.hp < hp[1]

    stab = [run("dirk_stab", s) for s in range(25)]
    assert not any(g for _a, g in stab), "dirk_stab was intercepted"
    assert any(a for a, _g in stab), "premise: dirk_stab lands sometimes"

    control = [run("harvest", s) for s in range(25)]
    assert any(g for _a, g in control), \
        "control: an ordinary attack must still be intercepted"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
