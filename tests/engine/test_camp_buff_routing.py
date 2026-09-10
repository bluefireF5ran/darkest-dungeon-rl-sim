"""A camp `buff` was scored sign-blind and aimed at nobody in particular.

Third finding of the 2026-08-23 camping sweep, and the one that had two
defects stacked on it.

**Sign.** `_effect_value` gave every `buff` effect a flat `+W_BUFF`, whatever
the buff did. Most camp buffs are good, but not all: the Runaway's
`play_with_fire` grants `+10% damage RECEIVED` and her `cauterize` grants
`-20% debuff resist`. Both scored as benefits, so the greedy spend could buy
them with points that had somewhere better to go.

**Target.** `_pick_individual` had branches for both heal types, both damage
types and both DoT cures, and none for `buff` -- so it fell through to
`candidates[0]`, the frontmost living hero. Defensible for a defensive buff,
arbitrary for an offensive one, and actively wrong for the harmful ones above,
which it aimed at the hero standing in the most danger.

Only the ROUTING is a convention. Which side a buff falls on is read off its
own `BuffDefinition.stat_type`, using the same "received axis" rule the
observation uses for its stat buckets: on `stress_dmg_received_percent` or
`damage_received_percent`, a POSITIVE amount is worse for the receiver.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store

from data.types import CampingSkillEffect
from engine.camping import (W_BUFF, _buff_nature, _effect_value,
                            _pick_individual)
from engine.combat_engine import CombatEngine
from engine.rng import SeededRNG

store = get_store()


def _eff(buff_id):
    return CampingSkillEffect(target="individual", amount=0.0, stat_type="buff",
                              stat_sub_type=buff_id, duration=None)


def _party():
    eng = CombatEngine(store, SeededRNG(0))
    eng.reset([store.heroes["crusader"]] * 4,
              [next(iter(store.enemies.values()))])
    return eng.heroes


def test_the_harmful_camp_buffs_still_exist_in_the_data():
    """The premise. Both are Runaway (Fire's Edge) skills, so this also fails
    loudly if the DLC stops loading."""
    harmful = [bid for bid in ("rw_play_with_fire_dmg_taken",
                               "rw_cauterize_debuff_resist")
               if bid in store.buffs]
    assert harmful, "the harmful camp buffs are gone; this fix is now dead code"
    for bid in harmful:
        assert _buff_nature(_eff(bid), store)[0] is False, bid
    print(f"  PASS test_the_harmful_camp_buffs_still_exist_in_the_data ({len(harmful)})")


def test_a_negative_on_a_received_axis_reads_as_BENEFICIAL():
    """The trap in the sign rule, and the reason it is not just `amount >= 0`.
    `campingStressResistBuff` is **-0.30** on `stress_dmg_received_percent` --
    taking 30% less stress. A naive sign test calls the best defensive buff in
    the game a debuff."""
    beneficial, offensive = _buff_nature(_eff("campingStressResistBuff"), store)
    assert beneficial is True
    assert offensive is False
    print("  PASS test_a_negative_on_a_received_axis_reads_as_BENEFICIAL")


def test_the_score_is_signed():
    heroes = _party()
    good = _effect_value(_eff("campingACCBuff"), heroes, [0], store)
    bad = _effect_value(_eff("rw_play_with_fire_dmg_taken"), heroes, [0], store)
    assert good == W_BUFF
    assert bad == -W_BUFF
    print("  PASS test_the_score_is_signed")


def test_an_unresolvable_buff_keeps_the_old_behaviour():
    """A buff id the store does not carry must not change how it is scored or
    aimed -- the fix should be invisible where it cannot apply."""
    heroes = _party()
    # `reset()` auto-resolves enemy turns before the first hero acts, so a
    # freshly built party is NOT necessarily at full HP -- level the bar
    # explicitly rather than assuming it.
    for h in heroes:
        h.hp = h.max_hp
    e = _eff("no_such_buff_id_at_all")
    assert _buff_nature(e, store) == (True, False)
    assert _effect_value(e, heroes, [0], store) == W_BUFF
    # Classified beneficial-defensive, so it routes to the lowest HP fraction;
    # with the party level that is the frontmost hero, exactly as before.
    assert _pick_individual(e, heroes, [0, 1, 2, 3], store) == [0]
    # ...and with no store at all, the literal pre-fix fall-through.
    assert _pick_individual(e, heroes, [0, 1, 2, 3], None) == [0]
    print("  PASS test_an_unresolvable_buff_keeps_the_old_behaviour")


def test_routing_by_what_the_buff_actually_does():
    heroes = _party()
    heroes[1].hp = 5                     # closest to dying
    heroes[3].dmg_max = 99               # biggest hitter
    cand = [0, 1, 2, 3]

    assert _pick_individual(_eff("campingACCBuff"), heroes, cand, store) == [3], \
        "an offensive buff should go to the biggest hitter"
    assert _pick_individual(_eff("campingDMGHighBuff"), heroes, cand, store) == [3]
    assert _pick_individual(_eff("campingDEFBuff"), heroes, cand, store) == [1], \
        "a survivability buff should go to the hero closest to dying"
    assert _pick_individual(_eff("campingBleedResistBuff"), heroes, cand, store) == [1]

    harmful = _pick_individual(_eff("rw_play_with_fire_dmg_taken"),
                               heroes, cand, store)[0]
    assert harmful != 1, "a harmful buff was aimed at the hero closest to dying"
    print("  PASS test_routing_by_what_the_buff_actually_does")


def test_a_harmful_buff_goes_to_whoever_can_absorb_it():
    """Mirrors the rule `stress_damage_amount` already follows: harmful
    `individual` effects land on the hero best able to take them."""
    heroes = _party()
    for i, frac in enumerate((0.2, 0.4, 1.0, 0.6)):
        heroes[i].hp = max(1, int(heroes[i].max_hp * frac))
    pick = _pick_individual(_eff("rw_cauterize_debuff_resist"),
                            heroes, [0, 1, 2, 3], store)
    assert pick == [2], f"expected the full-HP hero, got {pick}"
    print("  PASS test_a_harmful_buff_goes_to_whoever_can_absorb_it")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
