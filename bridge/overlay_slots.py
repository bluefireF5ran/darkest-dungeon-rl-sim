"""Which button in the game's action bar an action means.

A two-constant module because both sides need it and neither may import the
other: `bridge/run.py` decides the slot (it has the save and the engine),
`bridge/overlay.py` draws it (it has the screen). Importing the overlay from the
loop would drag tkinter into every headless run — `run.py --replay` and the
whole terminal path have no display and must not need one.

Left to right, the bar is: the hero's four equipped skills in the order the save
lists them, then the game's own MOVE button, then the narrow X, which is PASS.
Provisions are not on it — they come from the inventory — and are the one
recommendation with no button to point at.
"""

# 1-4 are the equipped skills; these two are the fixed extras at the right end.
MOVE_SLOT = 5
PASS_SLOT = 6

#: RETREAT is not on the action bar at all -- it is the raid HUD's X, top
#: left, the same control that abandons the quest out of combat. The game says
#: so itself, in three separate help strings: `[pc]str_help_raid_combat_4`
#: reads *"[CLICK] on the retreat icon (looks like an "X") to attempt to
#: retreat from the combat"*, and `str_help_raid_room_5` / `_hallway_7` name
#: the same icon for abandoning.
#:
#: It is numbered past the bar rather than left as -1 so that a caller can
#: tell "the advice is retreat" from "the advice has no control", which used
#: to be the same answer. `layout.RAID_ABANDON_QUEST` is where it is.
RETREAT_SLOT = 7
