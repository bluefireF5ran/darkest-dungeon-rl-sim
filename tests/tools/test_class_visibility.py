"""The gate that decides which classes may enter the training corpus.

`tools/class_visibility.py` refuses a class the policy cannot see. Three ways
to be invisible, and the third is the one nobody would guess:

* the skill's 45-float payload is all zeros, so it reads as a bare attack;
* the effects it names are missing from the store, so they do nothing;
* it has more skills than `MAX_SKILL_SLOTS`, so the extras have no action
  index and can never be chosen however well they are described.

What these tests pin is that **the thresholds still admit the vanilla
roster**. That is the whole calibration argument: 9% of vanilla hero skills
are payload-free plain attacks, so a class is not suspect for having one or
two, and a gate that would exclude a shipped class is measuring the wrong
thing. If a future observation change makes a vanilla class fail here, the
gate is wrong before the class is.

These run MODS OFF, like the rest of the suite. The modded numbers quoted in
the tool's docstring are not asserted here -- they are a property of whichever
Workshop mods happen to be subscribed, which is not a thing a test may depend
on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests._fixtures import get_store                                  # noqa: E402
from env.gym_env import MAX_SKILL_SLOTS, DarkestDungeonEnv       # noqa: E402
from tools.class_visibility import (MAX_UNRESOLVED_SHARE,        # noqa: E402
                                    MIN_VISIBLE_SKILLS, audit)

_CACHE = {}


def _rows():
    if "rows" not in _CACHE:
        store = get_store()
        _CACHE["rows"] = audit(store, DarkestDungeonEnv(store=store))
    return _CACHE["rows"]


def test_every_vanilla_class_passes_its_own_gate():
    """The calibration argument, as an assertion.

    The thresholds are set against the shipped roster; a vanilla class failing
    would mean the gate is refusing the game itself.
    """
    failed = [(r["hero"], r["visible"], r["skills"], r["unresolved"])
              for r in _rows() if r["vanilla"] and not r["passes"]]
    assert not failed, (
        f"the gate refuses {len(failed)} SHIPPED class(es), so its thresholds "
        f"are wrong before the classes are: {failed}")
    print(f"  PASS all {sum(1 for r in _rows() if r['vanilla'])} vanilla "
          f"classes clear the gate")


def test_the_visibility_threshold_has_headroom_over_the_worst_vanilla_class():
    """A threshold sitting exactly on the worst shipped class is a threshold
    that will start refusing the game on the next observation change."""
    vanilla = [r for r in _rows() if r["vanilla"]]
    worst = min(r["visible"] for r in vanilla)
    assert worst >= MIN_VISIBLE_SKILLS, (
        f"worst vanilla class shows {worst} visible skills against a "
        f"threshold of {MIN_VISIBLE_SKILLS}")
    print(f"  PASS worst vanilla class has {worst} visible skills, "
          f"threshold {MIN_VISIBLE_SKILLS}")


def test_no_shipped_class_names_an_effect_the_store_lacks():
    """Vanilla is the control for the unresolved-share rule: the base game
    resolves everything, so any share above zero here is our loader, not a
    mod."""
    bad = [(r["hero"], r["unresolved"], r["missing_sample"])
           for r in _rows() if r["vanilla"] and r["unresolved"]]
    assert not bad, f"shipped classes with unresolved effects: {bad}"


def test_a_class_with_more_skills_than_slots_is_refused():
    """The third invisibility, and the one the observation cannot express.

    Actions `0..27` are `slot * RANKS + rank` over `MAX_SKILL_SLOTS` slots, so
    an eighth skill has no index at all. `tests/data/test_mod_heroes.py` found four
    Workshop classes shipping 9 to 14.
    """
    overflowing = [r for r in _rows() if r["skills"] > MAX_SKILL_SLOTS]
    assert all(not r["passes"] for r in overflowing), (
        "a class with unreachable skills passed the gate: "
        f"{[r['hero'] for r in overflowing if r['passes']]}")
    # ...and vanilla must not be in that set, or the constant is wrong.
    assert not [r for r in overflowing if r["vanilla"]], (
        "a SHIPPED class exceeds MAX_SKILL_SLOTS, so the constant is wrong")


def test_the_unresolved_rule_is_a_share_and_not_a_count():
    """A count refuses a fully visible class over one tooltip reference.

    Measured on the subscribed corpus: the classes whose only unresolved ids
    were tooltips read 2.9% and 7.4%; the ones actually missing mechanics read
    17.2%, 52.4% and 100%. A count of 1 cannot separate those and a share can.
    """
    assert 0.0 < MAX_UNRESOLVED_SHARE < 1.0, MAX_UNRESOLVED_SHARE
    # The cut must sit in the measured gap, not on either side of it.
    assert 0.074 < MAX_UNRESOLVED_SHARE < 0.172, (
        f"{MAX_UNRESOLVED_SHARE} is outside the gap between the worst "
        f"tooltip-only class (7.4%) and the best genuinely broken one (17.2%)")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
