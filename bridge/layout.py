"""The board's geometry as FRACTIONS of the game's client area.

    python bridge/layout.py                    # show the migrated layout
    python bridge/layout.py --resolve          # ...against the live window

## Why fractions

`bridge/overlay_layout.json` stores the eight rank points and six action-bar
slots as **absolute screen pixels**, valid only for the resolution they were
clicked at, and only while the game covers the whole desktop. `overlay.py`
guards that by comparing `Layout.screen` against the desktop size at startup and
refusing a mismatch. For a real *window* that model breaks completely: the
client area is smaller than the screen, it starts at an arbitrary origin, and
both change the moment the user drags or resizes.

DD scales its fixed 16:9 layout to fill whatever client rect it is given. That
is the same property `overlay.py` already relies on when it notes "the game's
own resolution is irrelevant -- 1280x720 on a 2560x1440 monitor puts the ranks
in the same screen positions". Stated properly, it means **a rank sits at a
fixed FRACTION of the client area**, which is portable across window size,
window position and screen resolution alike.

## The existing calibration is not thrown away

The absolute layout was clicked with the game borderless-fullscreen at
2560x1440, so at that moment client == screen == the recorded `screen` field.
Dividing each point by it recovers the fraction exactly. `from_overlay_json`
does that, so a good 12-click calibration survives the change with no reclick.

Deliberately tkinter-free, for the same reason `bridge/overlay_slots.py` is:
`run.py` and `act.py` must be able to reason about geometry without importing a
GUI toolkit.

## Aspect ratio

Fractions are exact while the aspect matches the one calibrated at, which is
16:9. DD pads rather than stretching, so on any other aspect the picture is
inset in the client rect and a plain fraction lands off-target.

`play_rect` computes that inset and `ResolvedLayout._pt` maps every point
through it, so other aspects are handled rather than merely reported. At 16:9
it returns the whole client rect and the mapping is unchanged — the correction
cannot disturb a calibration that was already right.

**The model behind it is assumed and not yet measured**: that DD pads the whole
UI symmetrically around one 16:9 picture. If instead it anchors HUD furniture to
the true screen edges, this fixes the board and breaks the HUD.
`aspect_warning` still fires off-aspect for exactly that reason.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

RANKS = 4
SKILL_SLOTS = 6                     # 4 equipped skills + MOVE + the narrow X

DEFAULT_PATH = Path(__file__).resolve().parent / "overlay_layout.json"
# The aspect the shipped calibration was clicked at. 2560x1440 = 1.777...
CALIBRATED_ASPECT = 16 / 9
ASPECT_TOLERANCE = 0.02

# ── measured HUD geometry (not user calibration) ──
#
# The eight rank points and six action-bar slots above come from a human
# clicking them (`overlay_layout.json`). The constants below did not: they were
# MEASURED off `captures/*.png` at 2560x1440 by edge-detecting the game's own
# frame lines, and verified by drawing the derived grid back over the capture.
# So they live in code, not in the calibration file -- there is nothing for a
# user to click, and a hand-clicked value would be strictly worse.
#
# The detection is in the session record; the short version is that a cell
# border is bright at EVERY y down the cell while an icon is not, so the
# min-over-y profile isolates the vertical frame lines and ignores content.
# It returned the SAME lines in all three captures that show the pack.

# The raid pack: `inventory/base.inventory.system_configs.darkest` declares
#   inventory_system_config: .type "raid" .max_slots 16 .use_stack_limits true
# and the grid renders those 16 as 2 rows of 8. Slot n in the save is cell n
# here, ROW-MAJOR: `end_of_battle_rewards_1_item.png` shows 2,2,6,12,6,6,8,3
# across the top row, which is exactly the save's slots 0-7 in order.
INVENTORY_COLS = 8
INVENTORY_ROWS = 2
INVENTORY_SLOTS = INVENTORY_COLS * INVENTORY_ROWS
# Left and right edge of the cell block: detected borders 1306.0 and 2159.3 px
# of 2560, giving a pitch of 106.66 px -- the seven interior borders fit that
# pitch to under half a pixel.
INVENTORY_X = (1306.0 / 2560, 2159.3 / 2560)
# Centre of each row: the horizontal frame lines came out at 1000/1201 and
# 1214/1399 of 1440.
INVENTORY_Y = (1100.5 / 1440, 1306.5 / 1440)

# The "You don't have enough room in inventory to take all" modal's OK.
# The box is 880..1644 x 413..911 px; OK sits on its centre line, 1262,779.
# There is ~130px of dead space below OK inside the box, so the box is a fixed
# size rather than flowed around the message -- but that is inferred from ONE
# capture, so `act.py` still verifies the click by its effect on the save.
MODAL_OK = (1262.0 / 2560, 779.0 / 1440)

# The loot window's red X (decline / close). Found by colour -- it is the only
# saturated red 57x57 block on the right of the screen -- at exactly
# (1954, 775) in BOTH `end_of_battle_rewards_1_item.png` (Victory, one item)
# and `rewards_4_items.png` (Treasure, four items). Two different panels, two
# different item counts, same pixel: the item area is a fixed-height region, so
# the button row under it does not move.
#
# The CURIO panel is a different story and deliberately has no constant here.
# Its X came out at (1950, 618) -- same x, 157px higher -- because that panel
# flows around a description of whatever length the curio has. Its x is
# right-anchored and stable; its y is not, and inventing one would put a click
# on the description text. `bridge/curios.py` decides what to do at a curio;
# ACTING on it needs the panel found on screen, which is a capability the
# actuator does not have yet.
#: The two answers to a raid EVENT panel -- Hunger is the one that matters,
#: and Fran hit it live on 2026-09-06: *"Right now its stuck on a random
#: hunger check"*, *"its rare on the tutorial but can happen"*. The modal
#: blocks EVERYTHING: the party cannot walk, `W` does nothing, and the bridge
#: read that as a curio refusing to open, ten presses running.
#:
#: Edge-detected off `captures/screens/hunger.png` -- the lit boxes in the
#: option row are at x 863-891 and 1040-1050 of 1366, centres 877.0 and
#: 1045.0. The refuse box is independently found by `curiopanel._red_blocks`
#: at 1039.2 (it is the red crossed plate), which is the cross-check that
#: this row is being measured and not guessed.
HUNGER_EAT = (877.0 / 1366, 405.0 / 768)
HUNGER_REFUSE = (1045.0 / 1366, 405.0 / 768)
LOOT_CLOSE = (1954.0 / 2560, 775.0 / 1440)

# "Default Party order" -- the button that puts the party back in the marching
# order it embarked with. The game names it `str_party_default_order` and ships
# its icon as `panels/party_order_button.png`, which is what located it: the
# 38x46 asset alpha-matched against a live frame at scale 1.35, score **0.9767**
# against a next-best-anywhere-else of 0.4778. Same box (2184,1080)-(2235,1142)
# in a room, in a corridor after a map click, in combat, and with the pack open,
# so it is HUD furniture and not part of a panel that flows.
#
# It matters because combat reorders the party and nothing put it back. The
# Move action swaps two heroes, and a Highwayman parked at rank 1 has two of
# his four skills greyed out (`pistol_shot` is .launch 432, `grape_shot_blast`
# is .launch 23) -- the next fight then opens with half a movepool. Reported
# live: "Didn't use the set order after the combat ... went stuck there as it
# tried to use a skill that cant be used in pos 1".
PARTY_ORDER = (2209.0 / 2560, 1111.0 / 1440)

# The raid-end choice. Completing the objective does not end the raid: DD puts
# up a "Quest Complete!" banner over TWO buttons and waits, which is where the
# bridge stopped every time -- `run.py` printed "no room advice to act on" at
# the one moment the room genuinely had nothing left to advise.
#
# Located the same way as PARTY_ORDER, but by `tools/find_button.py` rather
# than by hand, so it is reproducible: the shipped 124x69 assets
# `panels/quest_return_to_hamlet.png` and `panels/quest_continue_raid.png`
# alpha-matched against a live 1280x720 frame at scale 0.67, scoring **0.9778**
# and **0.9874**. Two independent checks that this is the real match rather
# than a confident coincidence: the pair share a y exactly, and their x are
# symmetric about 0.5 -- which is how the game draws a two-button choice, and
# neither fact was given to the matcher.
#
# That tool reproduces PARTY_ORDER's own hand-measured fraction to within a
# pixel from a different frame at a different resolution, which is the check
# that it measures what it claims to.
QUEST_RETURN_TO_HAMLET = (0.4324, 0.6778)
QUEST_CONTINUE_RAID = (0.5676, 0.6778)

# "Next" on the raid-results screen (Victory! / Quest Rewards), which is what
# stands between a finished raid and the Hamlet.
#
# There is no shipped asset for it -- the base game draws it as text, and the
# only `raid_results*.png` in the install belong to the Arena DLC -- so
# `find_button.py` cannot locate this one and it is MEASURED off a live frame
# instead. Isolated by HUE rather than brightness: the label is DD's red, and
# the panel frame directly above it is bright too, so a luminance threshold
# picks up the frame and lands the click on it. `r > 90 and r > 1.6*g and
# r > 1.6*b` gives a single clean block, x 551-725 and y 657-688 of 1280x720.
#
# x is pinned to 0.5 rather than to the measured 0.4991: the panel is
# centre-anchored, both the pixel centroid (0.4991) and the block's midpoint
# (0.4984) agree with that to within a pixel, and a centred element is the one
# case where the exact value is derivable rather than sampled.
RAID_RESULTS_NEXT = (0.5, 0.9353)

# The pause menu (ESC). Measured off `captures/screens/pause_menu.png` by
# luminance rows in the centre column, discarding the descender fragments and
# the two selection underlines that bracket the highlighted row.
#
# **These two are 37.5 px apart at 720p and one of them quits the game.** That
# is the whole reason both are recorded: a constant for the row we want is
# worth little without the distance to the row we must never hit, and
# `tests/bridge/test_menus.py` asserts the gap rather than trusting the measurement.
# x is pinned to 0.5 -- the menu is centre-anchored and every row measured
# 0.4992-0.5008, which is centred to within a pixel.
#
# EXIT_TO_DESKTOP is banked and DELIBERATELY UNREFERENCED, the same way
# TORCH_METER is: nothing may click it, and a test proves nothing does.
#: The way OUT of the Butcher's Circus, which is a screen the bridge can reach
#: by accident: the Circus marker is planted in the Hamlet right on top of the
#: stage coach and swallows a click aimed a few pixels low.
#:
#: There is no keyboard way back. ESC opens the pause menu, whose only exits
#: are to the main menu and to the desktop -- both measured live 2026-09-05,
#: along with the top-left compass, which opens the Banner Designer. What
#: works is the small lit Hamlet house drawn among the crowd at the bottom
#: right of the Circus scene.
CIRCUS_EXIT = (1290.0 / 1366, 594.0 / 768)

#: The red `<<<` at the left of the title bar, which is the way BACK out of
#: the outbound embark chain -- the estate map and the provisioner both draw
#: it and nothing in the Hamlet or the raid-results chain does.
#:
#: **A one-way walk table is what made this necessary.** `town.ADVANCE` says
#: what the FORWARD control is on each screen, and `to_town` walked it toward
#: the Hamlet -- so a night resuming on the estate map clicked "Provision",
#: which is the wrong direction and, with no party seated, does nothing at
#: all. Three iterations of 2026-09-06 died on
#: "estate_map did not respond to 'Provision' after 4 tries".
#:
#: Isolated by HUE, the same way `TOWN_CLOSE_PANEL` and `RAID_RESULTS_NEXT`
#: were: `r > 100 and r > 2g + 30 and r > 2b + 30` in the title row gives one
#: clean block of 113 px spanning x 159-171, y 56-73 of 1280x720. It is
#: **byte-identical on both screens** -- same pixel count, same span, same
#: centre -- which is the check that it is one widget and not two glyphs that
#: happen to look alike.
TITLE_BAR_BACK = (165.0 / 1280, 64.5 / 720)

PAUSE_EXIT_TO_MAIN_MENU = (0.5, 0.6722)
PAUSE_EXIT_TO_DESKTOP = (0.5, 0.7243)

# The activity log's close X, which is what stands between returning from a
# raid and the Hamlet. Isolated by HUE in the upper right of
# `captures/screens/town.png`: DD's close glyph is saturated red, and a
# luminance threshold there picks up the panel's rose decorations instead.
# Two red glyphs share that row -- x 1008 and x 1056 of 1280 -- and the LEFT
# one is the X; the right belongs to the roster panel.
TOWN_CLOSE_PANEL = (0.7874, 0.1467)

# The provisioner's shop grid, row-major, measured off a live frame. Only the
# two supplies a run cannot start without are named: the game states its own
# recommendation for one of them, warning below eight food on a short quest.
# Buying is verifiable three ways, which is why it is trusted -- shop stock
# falls, the pack fills, and the gold total drops by exactly the price.
PROVISION_FOOD = (645.0 / 1280, 159.0 / 720)
PROVISION_TORCH = (708.0 / 1280, 275.0 / 720)

# The torch meter, top centre. Two gestures the game documents on it:
#   str_reduce_torch_tip  "[SHIFT+CLICK] to reduce torch"
#   str_snuff_torch_tip   "[SHIFT+CTRL+CLICK] to snuff out torch"
# So SHIFT-CLICK is overloaded: on a PACK SLOT it discards, on THIS widget it
# lowers the light. They are far apart on screen (this is y 0.105, the pack is
# y 0.76+), but the overload is why `Step.mods` prints and why nothing sends a
# modified click without a measured target.
#
# The bar is centre-anchored and its LIT portion shrinks symmetrically as the
# torch burns down -- measured across four captures at 935-1624, 956-1603 and
# 977-1582 px, all with midpoint 1279.5. So the ends move and the centre does
# not, which makes the centre the only stable target. y is 151 in all four.
# The point lands on the brazier's base, inside both the brazier sprite and
# the bar.
#
# NOT YET USED, and not wired to anything. `bridge/torchlight.py` does produce
# a "shutdown" action, but `run.py` calls it with `about_to_win=state.in_battle`
# -- so out of combat the advisor never returns one, and `plan_room`'s shutdown
# branch is unreachable by construction. Actuating a shutdown means putting it
# in the BATTLE plan ("snuff, then swing"), which is a change to `plan_battle`
# and to what a battle `Update` carries. The coordinate is recorded here so the
# measurement is not lost when that happens.
TORCH_METER = (1280.0 / 2560, 151.0 / 1440)

#: The BRAZIER FLAME above the rail, and the reason there are two points.
#:
#: `TORCH_METER` was banked and, by its own comment above, never sent -- and
#: the first live run that sent it got nothing: the torch sat at 70 through a
#: full snuff and four SHIFT-click steps, and Fran confirmed from the screen
#: that it never moved. Measured afterwards on `captures/01_0_Starting_room.png`
#: (2560x1440, the frame the rail constant came from too): the flame runs y
#: 100-122 with its peak at 115, while y=151 samples `(26, 20, 20)` -- the dark
#: base of the brazier, below the lit widget.
#:
#: Which of the two DD actually accepts is a question only the game can answer,
#: so `ActRunner._snuff_torch` tries both and says which one moved the torch.
#: A stray click here is inert: the top-centre HUD strip carries nothing else.
TORCH_FLAME = (1280.0 / 2560, 111.0 / 1440)

# ── and then the game turned out to ship the answer ──────────────────────
#
# Both constants above were EYEBALLED off `captures/01_0_Starting_room.png`,
# and both were refuted live on 2026-09-06: a full SHIFT+CTRL snuff and a
# SHIFT step at each moved a torch sitting at 70 by nothing. Fran then
# confirmed the widget itself is fine -- "if you spam shift+click on the torch
# icon the torch will lower" -- which leaves the pixel, and `townlayout.py`'s
# lesson applies: DD ships its UI as block-layout `.darkest` files in a
# 1920x1080 design space, so this is derivable and never needed measuring.
#
# `scripts/layout/screen.raid.darkest`:
#
#     torch_layout: .pos_y 28 .gauge_offset 26 89 .gauge_size 400 4
#                   .flamepos 960 100
#     torch_info:   .mouseOverAreaOffset 0 0  .mouseOverAreaSize 200 130
#                   .stripMouseOverAreaOffset 0 70
#                   .stripMouseOverAreaSize 860 24
#
# **What it does NOT ship is what those offsets are anchored to, and that is
# the entire question.** Read `mouseOverAreaSize 200 130` as centred on the
# flame and the clickable box is design x 860..1060; read it top-left
# anchored, the way a close glyph is, and it is x 960..1160. The two readings
# do not overlap at their centres. `townlayout.py` found the same thing one
# layer down -- `confirm_dialog.base_pos` is centre-anchored in x while a
# close glyph is not -- and its rule is that anchoring is PER WIDGET and has
# to be established, never assumed.
#
# So both readings are offered and the GAME picks: `act.torch_targets` sends
# them in order and reads `torchlight` back from `persist.raid.json` between
# each, which is the same save-acknowledgement rule the town layer runs on. A
# stray click is inert -- the top-centre HUD strip carries nothing else, with
# `quest_info` at x 12..312 and `kill_count_display` at 1530 well outside the
# span used here.
#
# The two measured points stay at the head of the list, because they were
# refuted with a 20ms modifier dwell and `act.MOD_DWELL` is now 120ms: if one
# of them answers, the dwell was the bug and the geometry was right.
_D = (1920.0, 1080.0)


def _design(x: float, y: float) -> tuple[float, float]:
    """A 1920x1080 design point as the client-rect fraction everything speaks."""
    return (x / _D[0], y / _D[1])


# **THE DIAL, MEASURED LIVE AND CONFIRMED BY THE GAME'S OWN TOOLTIP
# (2026-09-21).** Read off a raid frame at 1280x720: the flame dial at the
# centre of the light bar sits at (641, 80). A CTRL+SHIFT click there walked
# the light 93 -> 68 -> 43 -> 18 -> 0 across four clicks, each one announced
# by DD itself -- `RADIANT LIGHT ( 93 )`, `SHADOWY ( 43 )`,
# `BLACK AS PITCH ( 0 )`. That readout is the acknowledgement the save cannot
# give: it still said 68 while the screen said 0, because `persist.raid.json`
# is written per TURN.
TORCH_DIAL = (641.0 / 1280, 80.0 / 720)

TORCH_CANDIDATES = (
    ("dial (measured live 2026-09-21)", TORCH_DIAL),
    ("meter (measured, refuted at 20ms)", TORCH_METER),
    ("flame (measured, refuted at 20ms)", TORCH_FLAME),
    ("flamepos", _design(960, 100)),
    ("gauge (pos_y + gauge_offset)", _design(986, 117)),
    ("icon box, top-left anchored", _design(1060, 165)),
    ("strip, centred on the flame", _design(960, 170)),
    ("strip, top-left anchored", _design(1390, 182)),
    ("strip, left of centre", _design(760, 170)),
)

# The raid HUD's ABANDON QUEST banner: the little scroll with an X hanging
# under the quest crest, top left. Measured off the first Darkest Dungeon
# entry (2026-09-01) at 1280x720, where the glyph centres on (52, 90).
#
# **This is the only way out of a raid that the bridge sends**, and it is
# safe in a way the pause menu is not: it raises DD's own "Are you sure you
# want to retreat?" dialog, so a stray click costs one No. The pause menu's
# neighbouring row quits to desktop, which is why `PAUSE_EXIT_TO_DESKTOP`
# has a test saying nothing may click it.
RAID_ABANDON_QUEST = (52.0 / 1280, 90.0 / 720)

# The red WAX SEAL above that scroll, and it is a different control.
# Fran, 2026-09-21, watching the bridge click the scroll and achieve nothing:
# *"do not click the scroll, click the red symbol"*.
#
# Measured on the live frame at 1280x720: the seal is the round crest at the
# very top left, spanning roughly x 33-77 and y 28-72, so its centre is
# (55, 50) with about 22 px of slack in every direction. Verified by clicking
# it with the quest complete -- the game went straight to `raid_results`
# within two seconds.
#
# **It is NOT the retreat X and must not be confused with it.**
# `RAID_ABANDON_QUEST` is what `plan_retreat` clicks INSIDE a fight, and that
# is documented by the game's own `[pc]str_help_raid_combat_4`. This one ends
# the RAID from the quest banner, out of combat. Clicking the scroll below it
# does nothing at all: tested with a six-second watch and the frame never
# moved beyond its own animation.
#
# And it takes NO confirm when the objective is met -- the results page comes
# straight up. `ActRunner._leave_raid` therefore confirms only if a modal
# actually appears, rather than sending `MODAL_OK` into whatever is drawn
# next.
RAID_QUEST_SEAL = (55.0 / 1280, 50.0 / 720)

# ── the front end: title -> save slot -> DLC -> difficulty -> loading ──
#
# Measured off `captures/00_2..00_8` at 2560x1440 the same way as the pack
# above, by the bounding box of the strongly-gold pixels inside a search
# window. Fran's own click list (2026-08-17) names each one; the capture that
# shows it is named per constant.
#
# `Start Campaign` is NOT here, because it landed on 1262,779 -- which is
# `MODAL_OK` to the pixel. DD centres its modal buttons the same way whether
# the modal is "you have no room for this loot" or "is this your first
# estate", so the constant already existed.

# `Campaign`, the only button on the title screen (`00_2_title.png`).
# Frame 1039..1519 x 1201..1315.
MENU_CAMPAIGN = (1279.0 / 2560, 1258.0 / 1440)

# The save-slot list (`00_3` / `00_7`). Row separators detected at y = 955 and
# 1137, so the pitch is 182 and each row's NAME BAR centre sits ~100px into it
# -- 1055 and 1237, against measured label centres of 1055 and 1239.
#
# **Only two rows are visible.** The list viewport runs 955..1319 and the
# screen is black below 1340; a third slot needs the scrollbar, which nothing
# here drives. `VISIBLE_SAVE_ROWS` is what makes that a refusal instead of a
# click into empty space.
SAVE_ROW_X = 1178.0 / 2560
SAVE_ROW_Y0 = 1055.0 / 1440
SAVE_ROW_PITCH = 182.0 / 1440
VISIBLE_SAVE_ROWS = 2

# The DLC page's confirm tick, bottom centre (`00_4_Select_DLCs.png`).
# 1236..1307 x 1244..1266. Every DLC ships already ticked, so this only
# accepts the list; it never toggles one.
MENU_DLC_OK = (1271.0 / 2560, 1255.0 / 1440)

# `Darkest` on the campaign-type list (`00_6_Difficulty.png`), 1093..1472 x
# 723..788. `Radiant` is one row up and `Bloodmoon` one down at a 65px pitch,
# but only the one Fran picks is recorded: an unused coordinate that has never
# been clicked is a liability, not an asset.
MENU_DIFFICULTY_DARKEST = (1282.0 / 2560, 755.0 / 1440)

# The loading screen's Continue crest (`00_8_old_road.png`), 1252..1287 x
# 1197..1231. This capture is NOT a town screen and not a quest card: creating
# a campaign drops the party straight into the Old Road, and `app.log` logs
# this click as `Continue pressed in Loading Screen` immediately before
# `as_raid_start_done_loading`.
MENU_LOADING_CONTINUE = (1269.0 / 2560, 1214.0 / 1440)

# Somewhere a click can always be sent without doing anything. Verified black
# (max channel value <= 9 over a 40x40 patch) on ALL NINE `00_*` captures,
# which is what makes it safe to poke an animation along without knowing what
# is on screen.
MENU_DEAD_ZONE = (0.05, 0.93)

# Looked up by the name `bridge/screen.py` recognises the screen by, so the
# recogniser's vocabulary and the click table cannot drift apart.
# The save list's scrollbar DOWN arrow, read off `savelist.png` at 1280x720
# and confirmed by driving it: see `menus.scroll_plan`. There is an up arrow
# above it that nothing needs -- the list opens at the top, so every target is
# reached by going down.
MENU_SAVE_SCROLL_DOWN = (0.8420, 0.8990)

MENU_POINTS = {
    "campaign": MENU_CAMPAIGN,
    "save_scroll_down": MENU_SAVE_SCROLL_DOWN,
    "dlc_ok": MENU_DLC_OK,
    "modal_ok": MODAL_OK,
    "darkest": MENU_DIFFICULTY_DARKEST,
    "loading_continue": MENU_LOADING_CONTINUE,
    "dead_zone": MENU_DEAD_ZONE,
}


@dataclass(frozen=True)
class FractionalLayout:
    """Every point as (fx, fy) in 0..1 of the client area.

    Sizes (`radius`, `skill_half`, `pass_half`) are fractions of client WIDTH,
    not of each dimension: they describe round/square hit targets, and scaling
    them by height as well would make them elliptical on a non-16:9 client.
    """
    hero: tuple[tuple[float, float], ...] = ()
    enemy: tuple[tuple[float, float], ...] = ()
    skills: tuple[tuple[float, float], ...] = ()
    card: tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    skill_half: float = 0.0
    pass_half: float = 0.0
    source_aspect: float = CALIBRATED_ASPECT

    @property
    def complete(self) -> bool:
        return len(self.hero) == RANKS and len(self.enemy) == RANKS

    @classmethod
    def from_overlay_json(cls, path: Path = DEFAULT_PATH) -> Optional["FractionalLayout"]:
        """Migrate the absolute `overlay_layout.json` into fractions.

        Returns None when the file is absent, unparseable, or has no `screen`
        to divide by -- an absolute layout with no recorded resolution cannot
        be converted, and guessing the resolution would silently misplace every
        point.
        """
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if raw.get("space") == "client":
            return cls._from_native(raw)

        screen = raw.get("screen") or ()
        if len(screen) != 2 or not all(screen):
            return None
        sw, sh = float(screen[0]), float(screen[1])

        def frac(points):
            return tuple((p[0] / sw, p[1] / sh) for p in points or ())

        return cls(
            hero=frac(raw.get("hero")),
            enemy=frac(raw.get("enemy")),
            skills=frac(raw.get("skills")),
            card=((raw.get("card") or (0, 0))[0] / sw,
                  (raw.get("card") or (0, 0))[1] / sh),
            radius=float(raw.get("radius") or 58) / sw,
            skill_half=float(raw.get("skill_half") or 46) / sw,
            pass_half=float(raw.get("pass_half") or 17) / sw,
            source_aspect=sw / sh,
        )

    @classmethod
    def _from_native(cls, raw: dict) -> "FractionalLayout":
        tup = lambda ps: tuple((float(a), float(b)) for a, b in ps or ())
        return cls(
            hero=tup(raw.get("hero")), enemy=tup(raw.get("enemy")),
            skills=tup(raw.get("skills")),
            card=tuple(raw.get("card") or (0.0, 0.0)),
            radius=float(raw.get("radius") or 0.0),
            skill_half=float(raw.get("skill_half") or 0.0),
            pass_half=float(raw.get("pass_half") or 0.0),
            source_aspect=float(raw.get("source_aspect") or CALIBRATED_ASPECT),
        )

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps({
            "space": "client",
            "hero": [list(p) for p in self.hero],
            "enemy": [list(p) for p in self.enemy],
            "skills": [list(p) for p in self.skills],
            "card": list(self.card),
            "radius": self.radius,
            "skill_half": self.skill_half,
            "pass_half": self.pass_half,
            "source_aspect": self.source_aspect,
        }, indent=2), encoding="utf-8")

    def resolve(self, win) -> "ResolvedLayout":
        """Bind to a live `bridge.window.GameWindow`, giving screen pixels."""
        return ResolvedLayout(self, win)


@dataclass
class ResolvedLayout:
    """A `FractionalLayout` bound to one window's current client rect.

    Cheap to rebuild, and it must be rebuilt whenever the window moves --
    a windowed game can be dragged mid-fight, and a cached rect offsets every
    click silently. Callers hold the FractionalLayout and re-resolve.
    """
    frac: FractionalLayout
    win: object

    def _pt(self, fx: float, fy: float) -> tuple[int, int]:
        """A layout fraction -> absolute screen pixel, through the play area.

        Everything in this class goes through here rather than calling
        `win.to_screen` directly, so the letterbox correction cannot be applied
        to some points and forgotten for others. `window.to_screen` stays the
        plain client-rect mapping it claims to be; the knowledge that DD insets
        its picture belongs here, with the calibrated aspect.

        Identical to `win.to_screen` whenever the client matches the calibrated
        aspect, which is every setup this has ever run on.
        """
        ox, oy, w, h = play_rect(*self.win.client_size, self.frac.source_aspect)
        left, top = self.win.client_origin
        return left + ox + int(round(fx * w)), top + oy + int(round(fy * h))

    def point(self, side: str, rank: int) -> Optional[tuple[int, int]]:
        """Screen pixel for a 0-based rank on `side` ("hero"|"enemy").

        Rank 1 is the FRONT rank on both sides -- the engine's numbering, the
        advisor's, and the game's own tooltips'. Reversing it mirrors every
        click onto the wrong target and nothing downstream would object.
        """
        pts = (self.frac.hero if side == "hero"
               else self.frac.enemy if side == "enemy" else ())
        if not (0 <= rank < len(pts)):
            return None
        return self._pt(*pts[rank])

    def skill_point(self, slot: int) -> Optional[tuple[int, int]]:
        """Screen pixel for a 1-based on-screen action-bar slot.

        1-4 are the four equipped skills IN SAVE ORDER, 5 is MOVE, 6 is the
        narrow X (PASS) -- `bridge/overlay_slots.py` owns those two constants.
        This is the game's own numbering, not the action space's.
        """
        pts = self.frac.skills
        if not pts or not (1 <= slot <= len(pts)):
            return None
        return self._pt(*pts[slot - 1])

    def inventory_point(self, slot: int) -> Optional[tuple[int, int]]:
        """Screen pixel for a 0-based raid inventory slot.

        Row-major over 2 rows of 8, matching the save's own slot numbering --
        `party.inventory.items` children are named "0".."15" and every one of
        399 captures has them contiguous from 0, so the save index IS the cell.
        """
        if not (0 <= slot < INVENTORY_SLOTS):
            return None
        col, row = slot % INVENTORY_COLS, slot // INVENTORY_COLS
        x0, x1 = INVENTORY_X
        pitch = (x1 - x0) / INVENTORY_COLS
        return self._pt(x0 + pitch * (col + 0.5), INVENTORY_Y[row])

    @property
    def modal_ok(self) -> tuple[int, int]:
        """Screen pixel for the OK button of the 'no room to take all' modal."""
        return self._pt(*MODAL_OK)

    @property
    def loot_close(self) -> tuple[int, int]:
        """Screen pixel for the loot window's red X (leave the rest behind)."""
        return self._pt(*LOOT_CLOSE)

    @property
    def party_order(self) -> tuple[int, int]:
        """Screen pixel for "Default Party order" -- see `PARTY_ORDER`."""
        return self._pt(*PARTY_ORDER)

    @property
    def quest_return_to_hamlet(self) -> tuple[int, int]:
        """Screen pixel for "Return to Hamlet" on the quest-complete banner.

        This is the END of a raid, so it is the one HUD button here that
        forfeits whatever is still on the map. `quest_continue_raid` is the
        other half of the same choice and is what a party with rooms left
        should press; deciding between them is the caller's job and
        deliberately not this module's.
        """
        return self._pt(*QUEST_RETURN_TO_HAMLET)

    @property
    def quest_continue_raid(self) -> tuple[int, int]:
        """Screen pixel for "Continue Raid" -- see `quest_return_to_hamlet`."""
        return self._pt(*QUEST_CONTINUE_RAID)

    @property
    def pause_exit_to_main_menu(self) -> tuple[int, int]:
        """Screen pixel for "Exit to Main Menu" on the ESC menu.

        The caller must have CONFIRMED the pause menu is up first. This row
        sits 37.5 px above "Exit to Desktop" at 720p, so a click sent at a
        screen that merely resembles the pause menu can quit the game.
        """
        return self._pt(*PAUSE_EXIT_TO_MAIN_MENU)

    @property
    def title_bar_back(self) -> tuple[int, int]:
        """Screen pixel for the title bar's `<<<` -- see TITLE_BAR_BACK."""
        return self._pt(*TITLE_BAR_BACK)

    @property
    def circus_exit(self) -> tuple[int, int]:
        """Screen pixel for the little Hamlet house that leaves the Circus."""
        return self._pt(*CIRCUS_EXIT)

    @property
    def town_close_panel(self) -> tuple[int, int]:
        """Screen pixel for the activity log's close X -- see TOWN_CLOSE_PANEL."""
        return self._pt(*TOWN_CLOSE_PANEL)

    @property
    def provision_food(self) -> tuple[int, int]:
        """Screen pixel for the food row of the provisioner's shop."""
        return self._pt(*PROVISION_FOOD)

    @property
    def provision_torch(self) -> tuple[int, int]:
        """Screen pixel for the torch row of the provisioner's shop."""
        return self._pt(*PROVISION_TORCH)

    @property
    def raid_results_next(self) -> tuple[int, int]:
        """Screen pixel for "Next" on the raid-results screen.

        Safe to press repeatedly in a way the raid-end pair is not: it only
        advances a summary the player has already earned.
        """
        return self._pt(*RAID_RESULTS_NEXT)

    @property
    def raid_quest_seal(self) -> tuple[int, int]:
        """The red wax seal on the quest banner: ends the raid, out of combat.

        Distinct from `raid_abandon_quest`, which is the scroll under it and
        is the in-combat retreat control. See `RAID_QUEST_SEAL`.
        """
        return self._pt(*RAID_QUEST_SEAL)

    @property
    def raid_abandon_quest(self) -> tuple[int, int]:
        """Screen pixel for the raid HUD's abandon-quest banner.

        Clicking it only ASKS -- DD then draws "Are you sure you want to
        retreat?" with Yes on `MODAL_OK` -- so the irreversible half is the
        confirm, not this.
        """
        return self._pt(*RAID_ABANDON_QUEST)

    @property
    def torch_meter(self) -> tuple[int, int]:
        """Screen pixel for the torch meter: SHIFT-CLICK reduces, SHIFT+CTRL
        snuffs. Nothing sends either yet -- see `TORCH_METER`."""
        return self._pt(*TORCH_METER)

    @property
    def torch_flame(self):
        """The lit brazier above the rail -- the other candidate. See
        `TORCH_FLAME`."""
        return self._pt(*TORCH_FLAME)

    def at(self, frac: Optional[tuple[float, float]]) -> Optional[tuple[int, int]]:
        """A layout FRACTION -> screen pixel, or None for None.

        The public form of `_pt`, and the seam `bridge/townplan.py`'s geometry
        arrives through: `bridge/townlayout.py` derives the town's points from
        the game's own 1920x1080 layout files and hands them over as the same
        client-rect fractions everything here already speaks, so they get the
        identical letterbox correction as a hand-clicked rank point.

        **None passes straight through**, because that is how a derived point
        reports that it could not be read -- and turning it into (0, 0) here
        would convert a refusal into a click on the top-left of the screen.
        Every caller therefore checks the result exactly once, at the point of
        use, instead of guarding the lookup and the resolution separately.
        """
        return self._pt(*frac) if frac is not None else None

    def menu_point(self, name: str) -> Optional[tuple[int, int]]:
        """Screen pixel for a named front-end button, or None if unknown.

        Keyed by name rather than exposed as six properties because
        `bridge/menus.py` looks them up from a table indexed by the screen it
        recognised -- a property per button would just be a second table to
        keep in step with the first.
        """
        pt = MENU_POINTS.get(name)
        return self._pt(*pt) if pt else None

    def save_row(self, slot: int) -> Optional[tuple[int, int]]:
        """Screen pixel for the save-slot list's row `slot` (0-based).

        `slot` is the game's own slot INDEX -- the `N` in `profile_N` -- not a
        position in a list of campaigns the player happens to have. The two
        agree because DD renders the rows in slot order and puts the empty
        letter at the first free index: with `profile_0` used and `profile_1`
        free, the letter is row 2, and after creation the new campaign is row 2.
        Both captures show exactly that.

        Returns None past `VISIBLE_SAVE_ROWS`, because the list viewport holds
        two rows and reaching a third needs the scrollbar. Guessing a y below
        the fold would click black space, or worse, whatever the game draws
        there at some other resolution.
        """
        if not (0 <= slot < VISIBLE_SAVE_ROWS):
            return None
        return self._pt(SAVE_ROW_X, SAVE_ROW_Y0 + SAVE_ROW_PITCH * slot)

    @property
    def card(self) -> tuple[int, int]:
        return self._pt(*self.frac.card)

    @property
    def radius_px(self) -> int:
        return max(1, int(round(self.frac.radius * self.win.client_size[0])))

    @property
    def client_aspect(self) -> float:
        w, h = self.win.client_size
        return (w / h) if h else 0.0

    @property
    def aspect_warning(self) -> Optional[str]:
        """Non-None when the client aspect differs from the calibrated one.

        DD letterboxes rather than stretching, so at a different aspect the
        play area is inset inside the client rect and a plain fraction lands
        off-target. Reported rather than corrected: the inset depends on how
        the game chooses to letterbox, and inventing a correction that has not
        been observed would be a convention masquerading as measurement.
        """
        got, want = self.client_aspect, self.frac.source_aspect
        if want and abs(got - want) / want > ASPECT_TOLERANCE:
            ox, oy, w, h = play_rect(*self.win.client_size, want)
            return (f"client aspect {got:.3f} differs from the calibrated "
                    f"{want:.3f} - points are being mapped through the inset "
                    f"play area ({w}x{h} at +{ox},+{oy}), which ASSUMES DD pads "
                    f"the whole UI. Unverified at this aspect: check a known "
                    f"HUD point before trusting a click.")
        return None


def play_rect(client_w: int, client_h: int,
              aspect: float = CALIBRATED_ASPECT) -> tuple[int, int, int, int]:
    """`(x, y, w, h)` of the letterboxed play area inside a client rect.

    DD renders at one aspect and pads rather than stretching, so on a client
    that is not that aspect the picture is inset: bars left and right when the
    window is wider (pillarbox), top and bottom when it is taller (letterbox).
    A fraction of the *client* then lands off-target, which is what
    `aspect_warning` has been reporting rather than fixing.

    **At the calibrated aspect this returns the whole client rect**, so every
    existing 16:9 setup maps exactly as before — the correction cannot perturb
    a calibration that was already right. The tolerance is the same one
    `aspect_warning` uses, so "close enough to not warn" and "close enough to
    not inset" are the same judgement rather than two that can disagree.

    **The model is assumed, not yet measured.** It says DD pads the entire UI
    symmetrically around one 16:9 picture. Plenty of games instead letterbox
    the world and anchor HUD furniture to the true screen edges — and this HUD
    has corner-anchored furniture (`PARTY_ORDER` at x=0.863, `LOOT_CLOSE` at
    x=0.763). If DD does that, this fixes the board and breaks the HUD. One
    frame at a non-16:9 size settles it: see `tests/bridge/test_layout_letterbox.py`.
    """
    if client_w <= 0 or client_h <= 0 or aspect <= 0:
        return 0, 0, client_w, client_h
    got = client_w / client_h
    if abs(got - aspect) / aspect <= ASPECT_TOLERANCE:
        return 0, 0, client_w, client_h
    if got > aspect:                      # wider: bars at left and right
        w, h = int(round(client_h * aspect)), client_h
    else:                                 # taller: bars at top and bottom
        w, h = client_w, int(round(client_w / aspect))
    return (client_w - w) // 2, (client_h - h) // 2, w, h


def load(path: Path = DEFAULT_PATH) -> Optional[FractionalLayout]:
    return FractionalLayout.from_overlay_json(path)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Show the fractional layout.")
    ap.add_argument("--path", default=str(DEFAULT_PATH))
    ap.add_argument("--resolve", action="store_true",
                    help="bind to the live game window and print screen pixels")
    ap.add_argument("--process", default="Darkest.exe")
    args = ap.parse_args(argv)

    lay = load(Path(args.path))
    if lay is None:
        print(f"no usable layout at {args.path}")
        return 1
    print(f"fractional layout from {args.path} (source aspect {lay.source_aspect:.4f})")
    for side in ("hero", "enemy"):
        for i, (fx, fy) in enumerate(getattr(lay, side)):
            print(f"  {side:5s} rank {i+1}   {fx:.4f}, {fy:.4f}")
    for i, (fx, fy) in enumerate(lay.skills, 1):
        label = {5: " (MOVE)", 6: " (PASS)"}.get(i, "")
        print(f"  slot  {i}        {fx:.4f}, {fy:.4f}{label}")

    if args.resolve:
        from window import find_window          # noqa: E402  (script-mode import)
        win = find_window(args.process)
        if win is None:
            print(f"\n{args.process} is not running - cannot resolve.")
            return 1
        r = lay.resolve(win)
        print(f"\nresolved against client {win.client_size[0]}x{win.client_size[1]} "
              f"at {win.client_origin}")
        for side in ("hero", "enemy"):
            pts = [r.point(side, i) for i in range(RANKS)]
            print(f"  {side:5s} {pts}")
        print(f"  slots {[r.skill_point(i) for i in range(1, SKILL_SLOTS + 1)]}")
        print(f"  modal OK {r.modal_ok}   loot X {r.loot_close}")
        print(f"  menu     " + "  ".join(
            f"{n}={r.menu_point(n)}" for n in sorted(MENU_POINTS)))
        print(f"  save rows {[r.save_row(s) for s in range(VISIBLE_SAVE_ROWS)]}")
        for row in range(INVENTORY_ROWS):
            lo = row * INVENTORY_COLS
            pts = [r.inventory_point(lo + c) for c in range(INVENTORY_COLS)]
            print(f"  pack {lo:2d}-{lo+INVENTORY_COLS-1:2d} {pts}")
        if r.aspect_warning:
            print(f"  WARNING: {r.aspect_warning}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
