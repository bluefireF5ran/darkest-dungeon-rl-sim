"""The plan: what the bridge is about to do, before it does any of it.

Split out of `bridge/act.py` on 2026-09-08, verbatim. Re-exported from
`bridge.act`.

`Step` is one input -- a key, a click, a drag, a wait. `InputPlan` is an
ordered list of them plus the reason for them, and it is the unit the whole
actuation layer is built on: every `plan_*` function returns one, the
`--confirm` flow prints one and waits, the dry-run backend executes one
against nothing, and `refuse()` builds the empty plan that says why the
bridge is declining to act.

Separating the PLAN from the SENDING is the design decision that makes the
bridge testable without a game running: `tests/bridge/test_act.py` builds
plans and inspects them, and never presses a key.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from .keys import VK_1, VK_2, VK_3, VK_4, VK_5, VK_A, VK_CONTROL, VK_D, VK_ESCAPE, VK_RETURN, VK_SHIFT, VK_SPACE, VK_T, VK_TAB, VK_W

@dataclass(frozen=True)
class Step:
    """One primitive input. `kind` is "key", "click" or "drag"."""
    kind: str
    vk: int = 0                              # key
    # `keys`: several held down TOGETHER for `hold`, released in reverse.
    # Fran's find, and it is the whole hallway strategy: holding D and W at
    # once walks the party right while interacting with whatever it passes,
    # so nothing has to compute where to stop. **Only safe once the party has
    # cleared the door it arrived through** -- W at that door walks them back
    # out, which is the loop `plan_hallway_sweep` refuses to start.
    vks: tuple[int, ...] = ()
    screen: tuple[int, int] = (0, 0)         # click/drag SOURCE, absolute
    # Where a drag ENDS. Unused by the other kinds. A drag is one Step rather
    # than a down/move/up triple because the three are meaningless apart: a
    # plan that got half-executed would leave the button held, and `Actuator`
    # stops on the first failed step.
    to: tuple[int, int] = (0, 0)
    label: str = ""
    mods: tuple[int, ...] = ()               # click: vks held down across it
    # How long to hold the key down. 0 means `KEY_HOLD`, a tap, which is right
    # for every discrete command (a skill digit, take-all, interact). MOVEMENT
    # is not discrete: DD walks the party for as long as A/D is held, so a tap
    # nudges it a few pixels and a corridor needs a real hold. Reported as
    # "pulso D y se movio ligeramente" and then missed -- twelve taps left the
    # party still in `coBC`.
    hold: float = 0.0
    # How long to wait AFTER this step before the next one in the same plan.
    # 0 means `STEP_GAP`. It exists because a plan's steps are not always
    # independent: on DD's campaign-type screen the click SELECTS `Darkest`
    # and the RETURN that follows confirms it, and at an 80ms gap the RETURN
    # arrives before the selection has registered. Seen live -- the same plan
    # worked on one run and left the screen sitting there on the next, which
    # is what a race looks like.
    after: float = 0.0

    def __str__(self) -> str:
        if self.kind == "key":
            held = f" ({self.hold:.1f}s)" if self.hold else ""
            return f"KEY  {_vk_name(self.vk):6s} {self.label}{held}"
        if self.kind == "keys":
            names = "+".join(_vk_name(v) for v in self.vks)
            held = f" ({self.hold:.1f}s)" if self.hold else ""
            return f"KEYS {names:6s} {self.label}{held}"
        if self.kind == "drag":
            return (f"DRAG {self.screen[0]:5d},{self.screen[1]:<5d} -> "
                    f"{self.to[0]:5d},{self.to[1]:<5d} {self.label}")
        held = "".join(f"{_vk_name(m)}+" for m in self.mods)
        return (f"CLICK {held}{self.screen[0]:5d},{self.screen[1]:<5d} "
                f"{self.label}")


@dataclass(frozen=True)
class InputPlan:
    """What to send, or why nothing will be sent.

    A plan is inspectable before it is executed, which is the whole basis of
    the dry-run stage: `--replay` builds one for every captured turn and
    reports what would have happened, with the game closed.
    """
    steps: tuple[Step, ...] = ()
    layer: str = ""
    why: str = ""
    refusal: str = ""
    #: `.is_continue_turn` -- the hero acts and KEEPS the turn, so this plan
    #: is not the whole turn and the actuator must come back for the rest.
    continues_turn: bool = False
    #: A refusal that the SAME turn key can resolve on its own -- ask again
    #: rather than spending the turn. See `ActRunner.handle`.
    transient: bool = False
    #: The SAVE cannot witness this one; the screen can. Set for a MOVE.
    screen_witness: bool = False

    @property
    def actionable(self) -> bool:
        return bool(self.steps) and not self.refusal

    def __str__(self) -> str:
        if self.refusal:
            return f"[{self.layer or 'none'}] REFUSE: {self.refusal}"
        head = f"[{self.layer}] {self.why}"
        return head + "".join(f"\n    {s}" for s in self.steps)


def _vk_name(vk: int) -> str:
    return {VK_1: "1", VK_2: "2", VK_3: "3", VK_4: "4", VK_5: "5",
            VK_A: "A", VK_D: "D", VK_T: "T", VK_W: "W", VK_SPACE: "SPACE",
            VK_ESCAPE: "ESC", VK_SHIFT: "SHIFT", VK_CONTROL: "CTRL",
            VK_RETURN: "ENTER", VK_TAB: "TAB"}.get(vk, f"0x{vk:02X}")


def refuse(reason: str, layer: str = "") -> InputPlan:
    return InputPlan(layer=layer, refusal=reason)
