"""The action space, and what each index means.

Split out of `env/gym_env.py` on 2026-09-08, verbatim. Re-exported from
there; `env/equivariant_policy.py` imports RANKS, MAX_SKILL_SLOTS and
ACTION_SPACE_SIZE from `env.gym_env` and must keep working.

**Both spaces are RANK-INDEXED** -- slot k is board rank k -- rather than
list-indexed. That is the single most important property here: the board
is positional, targets are named by rank, and a rank-indexed layout keeps
the observation aligned with what an action MEANS. The design it replaced
indexed into whatever `get_valid_actions()` happened to return that turn,
so index k meant a different skill and a different target every turn and
the policy could only learn a crude positional prior.

**Widening this space invalidates every checkpoint.** So does changing
what an existing index means, and that one is silent -- the width still
matches and the model is fed numbers it never saw.
"""

from __future__ import annotations

# ── Observation / action space geometry ──
#
# Both spaces are RANK-INDEXED (slot k == board rank k) rather than
# list-indexed. That's the single most important property here: the board is
# positional, targets are named by rank, and a rank-indexed layout keeps the
# observation aligned with what an action means.
#
# The previous design was a 50-float observation of only
# [hp%, position, stress, is_dd, is_dead] per hero and [hp%, position,
# is_dead] per enemy, paired with a Discrete(64) action that indexed into
# whatever get_valid_actions() happened to return that turn. That had three
# fatal properties, all confirmed by direct inspection:
#   1. It never encoded WHOSE TURN IT IS. The vector was identical whether
#      the Crusader or the Vestal was choosing, so the policy could not
#      condition on the actor at all.
#   2. It never encoded identity (hero class, enemy type) or ANY status —
#      no stun, mark, bleed, blight, guard, riposte, stealth or buff state.
#      Every combat mechanic the engine models was invisible to the agent.
#   3. Action index k meant a different skill/target every turn, so the
#      policy could only ever learn a crude positional prior.
# Statuses are encoded both as explicit flags AND implicitly through each
# combatant's live stats (dodge/prot/speed/crit), which is what makes buff
# and debuff effects observable without enumerating every buff id.
RANKS = 4
MAX_SKILL_SLOTS = 7        # every real hero class has exactly 7 skills

# Explicitly ordered — engine.IN_COMBAT_PROVISION_IDS is a set, so its
# iteration order is not part of its contract and must never be used to
# assign stable action indices.
PROVISION_ACTION_ORDER = ("bandage", "antivenom", "holy_water", "laudanum")

# Action layout (fixed semantics, masked per turn):
#   [0 .. 27]  skill_slot * RANKS + target_rank
#   [28 .. 31] move to rank
#   [32 .. 35] provision (PROVISION_ACTION_ORDER)
#   [36]       pass
#   [37]       retreat  <- added 2026-08-15; see below
#
# `ACTION_RETREAT` is APPENDED, never inserted: every index below it means what
# it always meant, so a saved checkpoint's learned mapping is still the right
# mapping for 0..36. It does widen the space, which no old checkpoint can be
# loaded into — that break is real and deliberate, and the reason it is worth
# taking is that a party unable to leave a losing fight overstates how dangerous
# the dungeon is, which is most of what the full-expedition SUCCESS number
# measures.
ACTION_MOVE_BASE = MAX_SKILL_SLOTS * RANKS
ACTION_PROVISION_BASE = ACTION_MOVE_BASE + RANKS
ACTION_PASS = ACTION_PROVISION_BASE + len(PROVISION_ACTION_ORDER)
ACTION_RETREAT = ACTION_PASS + 1
# ABANDON THE QUEST. Appended 2026-08-24, the same discipline `ACTION_RETREAT`
# was added under: every index below it still means what it meant, and the
# widening itself is a hard break, so no checkpoint from before this date loads.
#
# It is NOT retreat. A retreat leaves the room on the map and the party fights
# it again (see `_start_next_combat`); abandoning ends the expedition with the
# party alive and walks them home. That is the move a real player makes at half
# HP with no food and a hero already dead, and its absence was the measured
# cause of the long band's failure mode: the first death lands 48% of the way
# in, 2.07 more follow, and 81% of wipes happen two to three combats LATER --
# a party being marched into fights it had no way to decline.
#
# Only `ExpeditionSequenceEnv` has an expedition to abandon, so the base env
# masks it off via `_can_abandon()`. Its price is entirely emergent, exactly as
# retreat's is: it forfeits every remaining combat's win reward and
# REWARD_EXPEDITION_COMPLETE, and buys avoiding REWARD_HERO_DEATH.
ACTION_ABANDON = ACTION_RETREAT + 1
ACTION_SPACE_SIZE = ACTION_ABANDON + 1


def mask_fn(env):
    """The `ActionMasker` callable. Use this everywhere; never inline it.

    Two separate traps, and the codebase has now been bitten by both.

    The FIRST is why the callable form is used at all rather than
    `ActionMasker(env, "action_masks")`: sb3_contrib's string branch does
    `getattr(self.env, name)` — an already-bound method — and then calls it
    with `self.env` as an explicit argument, so any normal instance method
    raises `TypeError: takes 1 positional argument but 2 were given`.

    The SECOND is why this reaches through `.unwrapped`. The obvious callable,
    `lambda e: e.action_masks()`, relies on `gymnasium.Wrapper.__getattr__`
    forwarding unknown attributes down to the inner env. **Gymnasium removed
    that forwarding in 1.x** (`hasattr(gymnasium.core.Wrapper, "__getattr__")`
    is False in 1.3.0), so the moment the env is wrapped in anything — a
    `TimeLimit`, say — the callable raises

        AttributeError: 'TimeLimit' object has no attribute 'action_masks'

    on the first rollout step. That silently broke `train.py --expedition` and
    the fixed mode; `--full-expedition` survived only because it is the one
    mode deliberately NOT wrapped in `TimeLimit`. `requirements.txt` pinned
    `gymnasium>=1.0` with no ceiling, so it arrived as dependency drift rather
    than as anyone's edit. `tests/training/test_train_wiring.py` now pins all three.

    `.unwrapped` is correct for the unwrapped case too — `gym.Env.unwrapped`
    returns `self` — so this is safe to use unconditionally.
    """
    return env.unwrapped.action_masks()
