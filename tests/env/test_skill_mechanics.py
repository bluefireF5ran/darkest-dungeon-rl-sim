"""The observation encodes MECHANICS, not skill identity (2026-08-23).

Fran's redirect: the model should learn that **+DODGE is good** -- it avoids
damage, avoids crits, and avoids the stress a crit brings -- rather than that
"Invigorating Vapors is good". A modded class built around dodge then plays
correctly with no retrain, because nothing about the encoding is keyed on which
class is holding the skill.

The measurement that justified it: the skill slot used to be 10 floats
(`exists, legal_now, targets_allies, is_heal, is_aoe, atk, dmg_mod, crit,
is_stall_invalidating, is_continue_turn`) and **125 of 140 hero skills, 89.3%,
carry at least one payload none of them describes**. Two skills of one class
were separable only by the 20-class one-hot plus the slot index.

These tests pin three things: every payload feature is REACHABLE (a feature no
shipped skill can set is a wiring bug, not a rare mechanic), the payloads are
correct on named skills, and the class one-hot is really gone.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from env.gym_env import (DarkestDungeonEnv, HERO_SLOT_SCALARS, RANKS,
                         SKILL_MECHANIC_FEATURES, SKILL_SLOT_FEATURES,
                         STAT_BUCKETS, _stat_bucket)

store = get_store()

FEATURE_NAMES = (
    ["stun", "bleed", "blight", "burn", "heal", "heal_pct", "heal_dot",
     "stress_heal", "stress_dmg", "cure", "guard", "riposte", "stealth",
     "mark", "shuffle", "push", "pull", "self_move"]
    + [f"tgt_{b}" for b in STAT_BUCKETS]
    + [f"self_{b}" for b in STAT_BUCKETS]
    + ["use_limit", "kill", "shield", "clear_corpses",
       "torch_change", "summon", "clears_guard", "clears_control",
       "unstealth", "bonus_action", "dot_irresistible"]
)


def _env():
    e = DarkestDungeonEnv(store=store)
    e.reset(seed=0)
    return e


def test_the_feature_names_match_the_declared_width():
    """A cheap guard: this list is what every failure message below reads
    from, so a payload feature added without extending it would be reported
    under the wrong name."""
    assert len(FEATURE_NAMES) == SKILL_MECHANIC_FEATURES
    # The descriptive head grew 10 -> 20 on 2026-08-23 (cooldown, uses
    # remaining, and the two 4-bit rank-reach masks). The payload is still
    # appended AFTER it, which is what keeps FEATURE_NAMES aligned.
    assert SKILL_SLOT_FEATURES == 20 + SKILL_MECHANIC_FEATURES
    print("  PASS test_the_feature_names_match_the_declared_width")


def test_every_payload_feature_is_reachable_by_some_hero_skill():
    """A feature no shipped skill can ever set is a WIRING BUG, not a rare
    mechanic -- it means the field name is wrong, or the value lives somewhere
    this code does not look. That is exactly how the heal AMOUNT was found
    missing: Vestal's Divine Grace carries no healing *effect*, the amount is
    on the skill level, and the payload read as completely empty for her
    signature skill.
    """
    env = _env()
    seen = [False] * SKILL_MECHANIC_FEATURES
    for hero in store.heroes.values():
        for sk in hero.skills:
            if not sk.levels:
                continue
            v = env._skill_mechanics(sk.id, max(sk.levels))
            for i, x in enumerate(v):
                if abs(x) > 1e-9:
                    seen[i] = True
    # Two exceptions, both verified against the data rather than waved away:
    #   `kill`  -- `is_kill_effect` exists on 4 shipped effects, none of them
    #              on a hero skill (it is a monster/Death's-Door mechanism).
    #   `*_hp`  -- no shipped buff carries `stat_type max_hp` and no effect
    #              carries `max_hp_percent`; the bucket is kept so the target
    #              and self blocks stay the same 12 axes in the same order.
    # A feature NOT on this list that goes unreachable is a wiring bug: that is
    # how the missing heal amount was caught (Vestal's Divine Grace carries no
    # healing *effect*, so her signature skill read as an empty payload).
    EXPECTED_UNREACHABLE = {
        "kill",              # 4 shipped effects, none on a hero skill
        # Same shape as `kill`, and verified the same way: 71 MONSTER skills
        # summon and no hero skill does. It is not dead weight -- this payload
        # also describes the ENEMY capability pool, which is the only place
        # the observation can say "the thing in front of you makes more of
        # itself". Before it, a Necromancer read as a weak attacker.
        "summon",
        "tgt_hp", "self_hp", # no buff carries stat_type max_hp, no effect max_hp_percent
        # Unreachable BY CONSTRUCTION, not by accident: a status-chance buff
        # (`stun_chance`, `bleed_chance`, ...) is an ATTACKER-side bonus that
        # lowers the target's effective resist, so it can only ever land in the
        # self block. A `tgt_status_chance` would mean buffing the enemy's
        # ability to land stuns, which nothing does.
        "tgt_status_chance",
        # No hero skill boosts an ALLY's healing received; the healing buffs
        # that exist are all self-applied or come from trinkets.
        "tgt_healing",
    }
    dead = {FEATURE_NAMES[i] for i, ok in enumerate(seen) if not ok}
    assert dead <= EXPECTED_UNREACHABLE, (
        f"payload features no hero skill can set: {sorted(dead - EXPECTED_UNREACHABLE)}")
    print(f"  PASS test_every_payload_feature_is_reachable ({len(seen)} features)")


def test_named_skills_produce_the_payload_they_obviously_should():
    """Fran named Guard Dog and dodge specifically. Each row is a skill whose
    mechanic is not in dispute, so a failure here is this encoder, not a
    judgement call about the game.
    """
    env = _env()
    idx = {n: i for i, n in enumerate(FEATURE_NAMES)}
    cases = [
        ("houndmaster", "guard_dog", ["guard", "self_dodge"]),
        ("man_at_arms", "defender", ["guard", "self_prot"]),
        ("plague_doctor", "blinding_gas", ["stun"]),
        ("bounty_hunter", "flashbang", ["stun", "shuffle"]),
        ("occultist", "weakening_curse", ["tgt_dmg"]),
        ("crusader", "inspiring_cry", ["stress_heal"]),
        ("vestal", "divine_grace", ["heal"]),
        ("grave_robber", "shadow_fade", ["stealth"]),
        ("runaway", "firefly", ["burn"]),
    ]
    for hero_id, skill_id, wanted in cases:
        hero = store.heroes.get(hero_id)
        assert hero is not None, hero_id
        sk = next((s for s in hero.skills if s.id == skill_id), None)
        assert sk is not None, f"{hero_id} has no skill {skill_id}"
        v = env._skill_mechanics(skill_id, max(sk.levels))
        for name in wanted:
            assert abs(v[idx[name]]) > 1e-9, (
                f"{hero_id}/{skill_id}: {name} not detected; "
                f"got {[FEATURE_NAMES[i] for i, x in enumerate(v) if abs(x) > 1e-9]}")
    print(f"  PASS test_named_skills_produce_the_payload_they_should ({len(cases)} skills)")


def test_a_debuff_is_negative_to_its_target_and_a_buff_positive_to_its_holder():
    """The sign convention, and it is the whole reason the target and self
    buckets are kept apart: every bucket reads "better for whoever RECEIVES
    it", so the policy is never asked to infer an XOR against
    `targets_allies`.
    """
    env = _env()
    idx = {n: i for i, n in enumerate(FEATURE_NAMES)}
    curse = env._skill_mechanics("weakening_curse", 4)
    assert curse[idx["tgt_dmg"]] < 0, "a Weakening Curse must read negative to its target"
    dog = env._skill_mechanics("guard_dog", 4)
    assert dog[idx["self_dodge"]] > 0, "Guard Dog's dodge must read positive to its holder"
    print("  PASS test_a_debuff_is_negative_and_a_buff_positive")


def test_two_skills_of_one_class_are_now_distinguishable_without_the_one_hot():
    """The actual claim. Before this change the Vestal's four skills differed
    only in atk/dmg/crit and a heal flag, so "which Vestal skill" was carried
    by the class one-hot plus the slot index and nothing else.
    """
    env = _env()
    vestal = [s.id for s in store.heroes["vestal"].skills]
    payloads = {sid: tuple(env._skill_mechanics(sid, 4)) for sid in vestal}
    distinct = len(set(payloads.values()))
    assert distinct >= 4, (
        f"only {distinct} distinct payloads across {len(vestal)} Vestal skills")
    print(f"  PASS test_two_skills_of_one_class_are_distinguishable "
          f"({distinct}/{len(vestal)} distinct)")


def test_the_hero_slot_carries_no_class_identity():
    """Dropping the 20-class one-hot is what forces the policy to read
    mechanics, and it is the property that makes a modded class transferable
    -- it has no one-hot slot at all, so anything keyed on identity gave it
    nothing.
    """
    env = _env()
    for h in env.engine.heroes:
        vec = env._hero_slot_vec(h, is_actor=False)
        assert len(vec) == HERO_SLOT_SCALARS, (
            f"hero slot is {len(vec)} wide, expected {HERO_SLOT_SCALARS} "
            "-- has an identity block come back?")
    empty = env._hero_slot_vec(None, is_actor=False)
    assert len(empty) == HERO_SLOT_SCALARS
    assert not any(empty), "an empty rank must be all zeros"
    print("  PASS test_the_hero_slot_carries_no_class_identity")


def test_the_payload_memo_returns_the_same_vector():
    """It is memoised per `(skill_id, level)` because it runs for 7 slots on
    every env step. A memo that returned a shared MUTABLE list would let one
    caller corrupt every later observation, so this also checks the value is
    stable rather than merely present.
    """
    env = _env()
    a = list(env._skill_mechanics("guard_dog", 4))
    b = list(env._skill_mechanics("guard_dog", 4))
    assert a == b
    assert env._skill_mechanics("guard_dog", 4) is env._skill_mechanics("guard_dog", 4)
    print("  PASS test_the_payload_memo_returns_the_same_vector")


def test_the_stat_bucket_map_reads_the_sub_type_for_combat_stat_buffs():
    """`combat_stat_add`/`combat_stat_multiply` carry the real axis in the SUB
    type -- keying on `stat_type` alone collapses every one of them into a
    single meaningless bucket, which is most of the 60.7% of skills that carry
    a stat buff.
    """
    assert _stat_bucket("combat_stat_add", "defense_rating") == "dodge"
    assert _stat_bucket("combat_stat_multiply", "damage_low") == "dmg"
    assert _stat_bucket("defense_rating", "") == "dodge"
    assert _stat_bucket("speed", "") == "speed"
    assert _stat_bucket("not_a_stat", "") is None
    print("  PASS test_the_stat_bucket_map_reads_the_sub_type")


def test_the_threat_ratio_rises_with_the_board_and_ignores_the_stunned():
    """The quantity that decides rush-vs-stall. A stunned enemy is excluded
    because it does not act this round -- which is the entire reason stunning
    buys a stall turn, and the thing Fran pointed out about stunning all four
    at once (they recover together, with raised stun resist, and then all four
    swing).
    """
    env = _env()
    brawler = store.enemies["cultist_brawler_A"]
    crusader = store.heroes["crusader"]

    env.engine.reset(hero_defs=[crusader], enemy_defs=[brawler])
    one = env._threat_ratio()
    env.engine.reset(hero_defs=[crusader], enemy_defs=[brawler, brawler, brawler])
    three = env._threat_ratio()
    assert three > one, f"three enemies ({three}) must out-threaten one ({one})"

    for e in env.engine.enemies:
        e.is_stunned = True
    assert env._threat_ratio() == 0.0, "a fully stunned board threatens nothing this round"
    print(f"  PASS test_the_threat_ratio_rises_with_the_board (1 -> {one:.3f}, 3 -> {three:.3f})")


def test_the_observation_width_is_derived_not_hardcoded():
    """`CLAUDE.md`'s standing rule. The width moved 345 -> 539 in this change
    and every checkpoint before it stops loading; the point of deriving it is
    that the NEXT such change needs no test edits.
    """
    env = _env()
    from env.gym_env import (ENEMY_SLOT_SCALARS, GLOBAL_FEATURES, MAX_SKILL_SLOTS)
    expected = (RANKS * HERO_SLOT_SCALARS
                + RANKS * (ENEMY_SLOT_SCALARS + len(env._enemy_type_index))
                + MAX_SKILL_SLOTS * SKILL_SLOT_FEATURES
                + GLOBAL_FEATURES)
    assert env.observation_space.shape[0] == expected
    assert env._get_obs().shape[0] == expected
    print(f"  PASS test_the_observation_width_is_derived ({expected})")




# ---------------------------------------------------------------------------
# The transfer falsifier's own plumbing (2026-08-23). The claim the mechanic
# encoding makes is that a class the policy never trained on is still playable,
# and `build_comp_pool`'s two filters are what cut the comp set into the halves
# that question needs.
# ---------------------------------------------------------------------------

def test_the_hold_out_split_is_a_clean_partition():
    """Every comp lands in exactly one half, and the halves reconstruct the
    whole pool. A comp appearing in both -- or in neither -- would leak the
    held-out class into training and make the experiment answer nothing.
    """
    from env.expedition_env import build_comp_pool
    full = {n for n, _z, _h in build_comp_pool(store)}
    for cls in ("duelist", "runaway", "vestal"):
        held = frozenset({cls})
        seen = {n for n, _z, _h in build_comp_pool(store, hold_out_classes=held)}
        transfer = {n for n, _z, _h in build_comp_pool(store, only_classes=held)}
        assert not (seen & transfer), f"{cls}: comps in BOTH halves: {sorted(seen & transfer)[:3]}"
        assert seen | transfer == full, f"{cls}: {len(full - (seen | transfer))} comps in neither"
        assert transfer, f"{cls}: nothing to transfer to"
    print("  PASS test_the_hold_out_split_is_a_clean_partition")


def test_no_held_out_comp_fields_the_held_out_class():
    """The whole point. A single leaked comp and the policy has met the class."""
    from env.expedition_env import build_comp_pool
    for cls in ("duelist", "runaway", "vestal"):
        for name, _z, hero_ids in build_comp_pool(store, hold_out_classes=frozenset({cls})):
            assert cls not in hero_ids, f"{name} fields a {cls} but survived the hold-out"
    print("  PASS test_no_held_out_comp_fields_the_held_out_class")


def test_a_held_out_class_is_unreachable_by_ANY_identity_encoding():
    """Why "put the class one-hot back" cannot be the repair for zero-shot
    transfer, stated as a test because it was recommended out loud once.

    The falsifier came back +46.6pp against the mechanic encoding (2026-08-23)
    and `tools/transfer_eval.py` used to conclude "the class one-hot was doing
    real work after all". It cannot have been. `--hold-out-class` removes the
    class from the pool ENTIRELY, so during training that class's one-hot bit
    is 0 on every step of every episode -- the weights behind it get gradient
    only from weight decay. At eval it fires for the first time, an untrained
    input on an out-of-distribution pattern. Identity carries strictly LESS
    about an unseen class than mechanics do, not more.

    What the test can actually check is the premise: the class never appears,
    so any per-class input is dead for the whole run. The inference from there
    is arithmetic.
    """
    from env.expedition_env import build_comp_pool
    for cls in ("vestal", "duelist"):
        pool = build_comp_pool(store, hold_out_classes=frozenset({cls}))
        appearances = sum(hero_ids.count(cls) if hasattr(hero_ids, "count")
                          else list(hero_ids).count(cls)
                          for _n, _z, hero_ids in pool)
        assert appearances == 0, (
            f"{cls} appears {appearances}x in the held-out pool, so a per-class "
            "input for it is NOT dead and the argument above needs re-checking")
    print("  PASS test_a_held_out_class_is_unreachable_by_ANY_identity_encoding")


def test_holding_out_nothing_is_the_untouched_pool():
    """The default has to be byte-identical to no filtering at all, or every
    ordinary training run silently becomes a hold-out run."""
    from env.expedition_env import build_comp_pool
    assert build_comp_pool(store) == build_comp_pool(store, hold_out_classes=frozenset())
    print("  PASS test_holding_out_nothing_is_the_untouched_pool")


def test_the_env_honours_the_hold_out():
    """Plumbed all the way through, not just available on the helper. Both
    expedition envs take it, since `--full-expedition` is the mode every
    shipped checkpoint is trained in."""
    from env.expedition_env import ExpeditionEnv
    from env.expedition_sequence_env import ExpeditionSequenceEnv
    for cls in ("ExpeditionEnv", "ExpeditionSequenceEnv"):
        env = {"ExpeditionEnv": ExpeditionEnv,
               "ExpeditionSequenceEnv": ExpeditionSequenceEnv}[cls](
            store=store, hold_out_classes=frozenset({"duelist"}))
        fielded = {h for _n, _z, hs in env._hero_pool for h in hs}
        assert "duelist" not in fielded, f"{cls} still fields a duelist"
    print("  PASS test_the_env_honours_the_hold_out")


# ---------------------------------------------------------------------------
# Batch 3 (2026-08-23): everything Fran's audit of the observation turned up.
# Enemy movesets, turn order, skill reach and cooldowns, the buff-carried
# fields, and Horror on the enemy side.
# ---------------------------------------------------------------------------

def test_the_policy_can_see_what_a_monster_DOES():
    """The largest hole the audit found: of 22 EnemyDefinition fields the
    observation read TWO (`actions_per_round`, `enemy_types`). The policy knew
    a rank-2 enemy had 14 HP and hit for 8, and had no way to know whether it
    stuns, bleeds, marks, or heals its friends.

    Each row is a monster whose signature move is not in dispute.
    """
    env = _env()
    idx = {n: i for i, n in enumerate(FEATURE_NAMES)}
    cases = [
        ("swinetaur_A", "stun"),        # Bellow/Charge -- the Warrens stunner
        ("crone_A", "mark"),            # the Hag marks before the cauldron
        ("madman_A", "stress_dmg"),     # Horror, and nothing but
        ("cultist_witch_A", "push"),
    ]
    for mid, want in cases:
        if mid not in store.enemies:
            continue
        caps = env._enemy_capabilities(mid)
        assert abs(caps[idx[want]]) > 1e-9, (
            f"{mid}: {want} not detected; got "
            f"{[FEATURE_NAMES[i] for i, v in enumerate(caps) if abs(v) > 1e-9]}")
    print("  PASS test_the_policy_can_see_what_a_monster_DOES")


def test_monster_capabilities_are_read_from_the_monster_not_the_skill_index():
    """`engine.get_skill_data` goes through `DataStore.skill_index()`, which is
    built heroes-first with `setdefault` -- so a monster skill whose id collides
    with a hero's silently resolves to the HERO's numbers. `_enemy_capabilities`
    reads `EnemyDefinition.skills` directly for exactly that reason.

    This checks the hazard is real rather than theoretical, and that the enemy
    path does not go through the index.
    """
    hero_ids = {sk.id for h in store.heroes.values() for sk in h.skills}
    monster_ids = {sk.id for e in store.enemies.values() for sk in e.skills}
    collisions = hero_ids & monster_ids
    env = _env()
    for mid, ed in store.enemies.items():
        if any(sk.id in collisions for sk in ed.skills):
            # the capability must come from THIS monster's own level data
            direct = [0.0] * SKILL_MECHANIC_FEATURES
            for sk in ed.skills:
                if sk.levels:
                    v = env._mechanics_from_data(sk.levels[max(sk.levels)])
                    direct = [max(a, b, key=abs) for a, b in zip(direct, v)]
            assert env._enemy_capabilities(mid) == direct
            break
    print(f"  PASS test_monster_capabilities_read_from_the_monster "
          f"({len(collisions)} colliding skill ids)")


def test_the_turn_order_reaches_the_observation():
    """The single largest global that was missing. "Kill it before it swings"
    needs the queue, and the queue is SPEED + d8 rolled at reset -- so it is
    NOT a function of the visible stats and cannot be reconstructed."""
    env = _env()
    pos, enemies_first = env._turn_order_features(env.engine.get_current_actor_id())
    assert 0.0 <= pos <= 1.0 and 0.0 <= enemies_first <= 1.0
    assert env.engine.turn_order, "no queue at all"
    # An empty queue must degrade to zeros rather than raising.
    saved = env.engine.turn_order
    env.engine.turn_order = []
    assert env._turn_order_features(0) == [0.0, 0.0]
    env.engine.turn_order = saved
    print("  PASS test_the_turn_order_reaches_the_observation")


def test_skill_reach_is_encoded_as_rank_bits():
    """`launch` (ranks a skill can be used FROM) and `target` (ranks it can
    hit) appeared nowhere, so "if I move to rank 2 this opens up" was not
    something the policy could reason about -- it could only discover it by
    moving and watching the mask change."""
    from env.gym_env import _rank_bits
    assert _rank_bits([1, 2]) == [1.0, 1.0, 0.0, 0.0]
    assert _rank_bits([4]) == [0.0, 0.0, 0.0, 1.0]
    assert _rank_bits([]) == [0.0, 0.0, 0.0, 0.0]
    assert _rank_bits(None) == [0.0, 0.0, 0.0, 0.0]
    assert _rank_bits([9]) == [0.0, 0.0, 0.0, 0.0], "out-of-range rank must not write"
    print("  PASS test_skill_reach_is_encoded_as_rank_bits")


def test_a_limited_skill_reports_its_remaining_uses():
    """"legal right now" collapsed three different situations into one bit --
    on cooldown, out of uses, and no valid target."""
    from env.gym_env import _uses_remaining
    env = _env()
    hero = env.engine.heroes[0]

    class _SD:
        per_battle_limit = 2
    sd = _SD()
    hero.skill_uses = {}
    assert _uses_remaining(hero, "x", sd) == 1.0
    hero.skill_uses = {"x": 1}
    assert _uses_remaining(hero, "x", sd) == 0.5
    hero.skill_uses = {"x": 2}
    assert _uses_remaining(hero, "x", sd) == 0.0

    class _NoLimit:
        per_battle_limit = None
    assert _uses_remaining(hero, "x", _NoLimit()) == 1.0, "an unlimited skill is always 1.0"
    print("  PASS test_a_limited_skill_reports_its_remaining_uses")


def test_horror_is_visible_on_an_enemy_too():
    """`stress_dots` was on the HERO slot only, so a stress DoT riding an enemy
    was unobservable."""
    env = _env()
    e = env.engine.enemies[0]
    before = env._enemy_slot_vec(e)
    from engine.combatant import DotInstance
    e.stress_dots = [DotInstance(effect_name="Horror probe",
                                 damage_per_tick=3, remaining_ticks=2)]
    after = env._enemy_slot_vec(e)
    assert after != before, "a stress DoT on an enemy changed nothing in its slot"
    print("  PASS test_horror_is_visible_on_an_enemy_too")


def test_buff_carried_state_reaches_the_hero_slot():
    """A buff whose stat_type is not one of the live stats lands in its own
    field on `CombatantState`, and 21 of those were invisible -- so a "+20%
    stun chance" buff was observable from the SKILL side and not at all from
    the CARRIER side."""
    env = _env()
    h = env.engine.heroes[0]
    before = env._hero_slot_vec(h, False)
    h.stun_chance_bonus += 0.2
    h.stress_dmg_received_mult *= 1.5
    h.crit_received_bonus += 0.1
    after = env._hero_slot_vec(h, False)
    moved = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(moved) == 3, f"expected 3 features to move, {len(moved)} did"
    print("  PASS test_buff_carried_state_reaches_the_hero_slot")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
