"""The Hamlet's geometry, DERIVED from the game's own layout files.

Every other coordinate in `bridge/layout.py` was either clicked by a human
(`overlay_layout.json`) or edge-detected off a capture. Neither scales to the
town: there are ten buildings, each with its own panel, its own upgrade trees
and its own activity slots, and calibrating them one screen at a time is the
cost that kept the town at 2/10.

**It does not have to be paid.** Darkest Dungeon ships its complete UI layout
as `.darkest` block-layout files, in a **1920x1080 design space**, and this
repo's own `DarkestParser.parse_blocks` already reads them. The decisive one is
`building_navigation.layout.darkest`, which indexes every building's entry
button by name:

    building_navigation_layout: .button_start_position 0 0 .button_spacing 0 68
    stage_coach 0  blacksmith 1  guild 2  camping_trainer 3  tavern 4
    abbey 5  sanitarium 6  nomad_wagon 7  graveyard 8  statue 9

So "map out the coordinates for the other nine buildings" is one parser call.

## Why this is trustworthy, and how that is checked

A derivation is only as good as its agreement with something measured
independently, so the two constants in `bridge/layout.py` that these files also
describe are the check -- `tests/bridge/test_townlayout.py` fails if either drifts:

* **`MODAL_OK`** was edge-detected at (1262, 779) of 2560x1440, which is
  **(946.5, 584.2)** design px. `confirm_dialog.layout.darkest` gives
  `base_pos (960,200) + answers_offset (-12,-20) + start_offset (0,390)` and a
  `button_size` of 500x38, so the first answer's centre is **(948, 589)**.
  **1.5 px in x, 4.8 px in y.**
* **The raid pack's column pitch** was edge-detected as 106.66 px of 2560,
  which is **80.0** design px. `inventory_item_layout.icon_size` is 72 wide and
  `provision_store_grid_layout.offset` is **80** -- two independent files
  giving the same pitch, and the icon plus its 8 px gutter explaining it. The
  row pitch measures 206 px of 1440 = **154.5** design against an icon height
  of 144.

That agreement is the evidence that the files mean what this module reads them
to mean. It is the same discipline `CompSpace.viable_ranks` follows: a
derivation that reproduces a number nobody derived it from is measuring the
right thing.

## The rules of reading these files

* **A multi-value numeric comes back as a STRING** -- `'172 259'`, and
  sometimes tab-separated -- because that is what `DarkestParser` documents.
  Split at the call site, on whitespace, which handles both. `_xy` is that
  call site and nothing else parses one.
* **Offsets compose, and the composition is per-panel.** A panel's own file
  gives offsets relative to the panel; `town.layout.darkest`'s
  `town_screen_layout` gives where that panel sits on the screen. Getting the
  base wrong shifts a whole family of points together, which is exactly what
  the cross-check above catches.
* **A missing file yields no points, never a guess.** `_blocks` returns `{}`
  and every accessor returns `None`. A wrong coordinate clicks something; an
  absent one refuses, and `bridge/act.py` is built to refuse.
* **ANCHORING IS PER WIDGET, and it has to be established rather than
  assumed.** `confirm_dialog_layout.base_pos` is `960 200` -- 960 is exactly
  half of 1920 -- so that panel is centre-anchored in x, and the derived
  answer lands 1.5 px from the measured `MODAL_OK`. A close glyph is NOT: see
  `CLOSE_GLYPH_CENTRE` below, where the same comparison says `close_pos`
  anchors the top-left. The way to settle it is a point that was measured
  independently, which is why the cross-checks are load-bearing and not
  decoration.
* **Nothing here sends anything.** This module is pure geometry, the way
  `bridge/layout.py` is -- it has no window, no backend and no game state.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.darkest_parser import DarkestParser

from data.paths import GAME_DIR

# DD authors its UI at 1920x1080 and scales to the client rect. Every number in
# every file below is in that space, which is why one divisor serves all of
# them -- and why `bridge/layout.py`'s fractions are the natural target.
DESIGN_W, DESIGN_H = 1920.0, 1080.0

# The order `building_navigation.layout.darkest` assigns. Read from the file at
# runtime; this is the fallback and the documentation of what to expect, not a
# second source of truth.
# The wallet's four heirlooms, in the order `persist.estate.json` lists them
# and the order the exchange spinner cycles through them.
from data.heirlooms import HEIRLOOMS            # noqa: E402

BUILDINGS = ("stage_coach", "blacksmith", "guild", "camping_trainer", "tavern",
             "abbey", "sanitarium", "nomad_wagon", "graveyard", "statue")

_LAYOUTS = {
    "town":        "campaign/town/town.layout.darkest",
    "navigation":  "campaign/town/building_navigation/building_navigation.layout.darkest",
    "building":    "campaign/town/buildings/building.layout.darkest",
    "stage_coach": "campaign/town/buildings/stage_coach/stage_coach.layout.darkest",
    "roster":      "campaign/town/roster/roster.layout.darkest",
    "party":       "campaign/town/embark_party/embark_party.layout.darkest",
    "provision":   "campaign/town/provision/provision.layout.darkest",
    "exchange":    "campaign/town/heirloom_exchange/heirloom_exchange.layout.darkest",
    "confirm":     "shared/confirm_dialog/confirm_dialog.layout.darkest",
    "inventory":   "shared/inventory/inventory.layout.darkest",
    "activity_log": "campaign/town/activity_log/activity_log.layout.darkest",
    "quest_select": "campaign/town/quest_select/quest_select.layout.darkest",
    # The courtyard's map position is the ONLY thing the Crimson Court adds
    # here, and it is in a differently-named file. Read second, never first --
    # base wins on collision, the same rule `DataLoader`'s DLC merge follows.
    "quest_select_cc": ("dlc/580100_crimson_court/features/crimson_court/campaign"
                        "/town/quest_select/crimson_court.quest_select.layout.darkest"),
    "guild":       "campaign/town/buildings/guild/guild.layout.darkest",
    "blacksmith":  "campaign/town/buildings/blacksmith/blacksmith.layout.darkest",
}


def design_pt(x: float, y: float) -> tuple[float, float]:
    """A 1920x1080 design-space point as the client-rect fractions the rest of
    `bridge/` already speaks. The single bridge between the two worlds."""
    return (x / DESIGN_W, y / DESIGN_H)


def _xy(value) -> Optional[tuple[float, float]]:
    """A multi-value numeric -> `(x, y)`. None if it is not a pair.

    The parser hands these back as one string on purpose (a `.pos` can carry
    two or three components), so this is where that is undone. A
    three-component value -- `stage_coach_layout.pos` is `315 -5 -3`, x/y/depth
    -- keeps its first two, because the third is a render order and not a
    coordinate. `str.split()` with no argument splits on any whitespace, which
    is what handles the tab-separated ones.
    """
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    else:
        parts = str(value).split()
    if len(parts) < 2:
        return None
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError:
        return None


@lru_cache(maxsize=None)
def _blocks(key: str, game_dir: str) -> dict:
    """`{block type: record}` for one layout file, or `{}` if it is not there.

    Cached because these are read once per point and never change under a
    running game. Keyed on the game dir as a string so the cache stays correct
    when a test points at a different install.
    """
    path = Path(game_dir) / _LAYOUTS[key]
    try:
        records = DarkestParser.parse_blocks(path)
    except (OSError, ValueError):
        return {}
    return {r.get("_type", ""): r for r in records if r.get("_type")}


def _field(key: str, block: str, field: str,
           game_dir: Path = GAME_DIR) -> Optional[tuple[float, float]]:
    rec = _blocks(key, str(game_dir)).get(block)
    return _xy(rec.get(field)) if rec and field in rec else None


def _add(*points) -> Optional[tuple[float, float]]:
    """Compose offsets, refusing if ANY of them is missing.

    Silently treating an unreadable offset as (0, 0) is the failure this
    exists to prevent: it does not produce no point, it produces a point in
    the wrong place, which is the one outcome layer 3 must never have.
    """
    total_x = total_y = 0.0
    for p in points:
        if p is None:
            return None
        total_x += p[0]
        total_y += p[1]
    return (total_x, total_y)


def _pt(*points) -> Optional[tuple[float, float]]:
    """`_add` composed with `design_pt`: the shape every accessor ends in."""
    total = _add(*points)
    return design_pt(*total) if total is not None else None


def _index_ok(index, upper: Optional[int] = None) -> bool:
    """A grid index has to be a non-negative int, and inside the panel.

    **Negative index arithmetic is perfectly valid arithmetic**, which is what
    makes this necessary rather than defensive: `start + spacing * -1` is a
    real point, one row ABOVE the panel, and every accessor here returned it
    happily. Caught by `tests/bridge/test_act_town.py` asking for coach slot -1 and
    getting a plan instead of a refusal -- on screen that is a drag beginning
    on whatever the Hamlet draws up there.

    `bool` is rejected explicitly: `True` is an int and `slot(True)` is slot 1,
    which is the kind of call that comes from a mis-ordered keyword argument
    and would otherwise silently work.
    """
    if isinstance(index, bool) or not isinstance(index, int):
        return False
    if index < 0:
        return False
    return upper is None or index < upper


# -- the town screen ------------------------------------------------------

# Where a building is CLICKED on the Hamlet screen, as client-rect fractions.
#
# **THE NAVIGATION STRIP IS NOT DRAWN ON THE HAMLET, and this constant exists
# because a live run proved it.** `building_button` derives the strip's button
# perfectly well from `building_navigation.layout.darkest`; the first live town
# visit clicked it, nothing opened, and the loop then sent six more clicks into
# an open Hamlet -- every one correctly reported UNCONFIRMED by the save check,
# which is the only reason it was harmless. DD puts the strip inside a building
# panel (to hop between buildings); from the town screen you click the BUILDING
# IN THE SCENE.
#
# **The x IS derivable and the y is NOT.** `town.layout.darkest` gives each
# building a `pos`, and hue-detecting the Hamlet's red banners off a live frame
# (the method that measured `TOWN_CLOSE_PANEL`) matches `pos.x / 1920` to
# within two pixels: stage_coach 313 measured against 315 shipped, statue 939
# against 940, graveyard 960 against 965, nomad_wagon 1055 against 1040 once
# `alert_offset` is applied. The y does NOT fit -- the scene is a perspective
# projection with a per-building z, and `alert_offset` is in different units
# again (fitting stage_coach and graveyard predicts the statue 23 px out and
# the nomad wagon 146 px out). So the y is measured, like every other screen
# coordinate in `bridge/layout.py`.
#
# Measured off `live_panel.png`, a 1280x720 Hamlet captured mid-run: the stage
# coach's banner blobs centre on design (313.9, 788.0), which sits inside the
# wagon sprite (`bbox_offset 35 0`, `bbox_size 186 172`).
#
# The other nine are deliberately ABSENT rather than guessed. Each one needs a
# decision rule before it needs a coordinate, and `building_scene_point`
# refuses for anything not in here -- a refusal costs a town visit, a guessed
# coordinate costs whatever happens to be under it.
# **MEASURED ON THE WAGON, NOT ON THE BANNER.** `town.layout.darkest`'s
# `pos.x` is the building's banner and it cross-checks to within two pixels;
# what a click has to hit is the WAGON, which sits to its right and lower, and
# the y was never derivable at all.
#
# The first point here was (313.9, 788.0) and it **opened the Butcher's
# Circus**: the Circus marker is planted in the Hamlet just left of and below
# the coach, and its hotspot reaches over the shipped point. Two unattended
# iterations walked straight into it on 2026-09-05.
#
# Re-measured the same day by clicking and reading the resulting screen, at
# 1366x768. The coach answers inside roughly x 223-310, y 520-545, and
# nothing outside that did:
#
#     (223,520) coach   (250,540) coach   (290,530) coach   (310,530) coach
#     (223,560) CIRCUS  (254,550) CIRCUS  (254,505) miss    (200,530) miss
#     (370,520) miss    (340,555) miss
#
# So this is the centre of the answering box, verified twice, with about 13 px
# of margin in y at that height and 45 in x. A THIN margin in y is the honest
# state -- the wagon is small -- and `tests/bridge/test_townlayout.py` pins the point
# inside the measured box so a "tidy-up" cannot drift it back onto the Circus.
BUILDING_SCENE_POINTS = {
    "stage_coach": (266.0 / 1366, 532.0 / 768),
}

#: The box the coach answered in, at 1366x768, as client fractions. Kept so
#: the test can check the point rather than restate it.
STAGE_COACH_ANSWERS = ((223.0 / 1366, 310.0 / 1366),
                       (520.0 / 768, 545.0 / 768))


def building_scene_point(name: str):
    """Where to click on the HAMLET to open one building, or None.

    This -- not `building_button` -- is what opens a building from the town
    screen. See `BUILDING_SCENE_POINTS`.
    """
    return BUILDING_SCENE_POINTS.get(name)


def building_button(name: str, game_dir: Path = GAME_DIR):
    """The left-hand navigation button that opens one building.

    **NOT reachable from the Hamlet** -- the strip is drawn inside a building
    panel, so this is how to hop from one building to the next once one is
    open, and `building_scene_point` is how to open the first. A live run on
    2026-08-31 clicked this from the town screen and nothing happened.

    `button_index` comes out of the file rather than out of `BUILDINGS`, so a
    DLC that renumbers the strip (the Circus already has a layout block in
    `town.layout.darkest`) is followed rather than mis-clicked.
    """
    rec = _blocks("navigation", str(game_dir)).get(
        f"building_navigation_building_layout_{name}")
    if rec is None or "button_index" not in rec:
        return None
    try:
        index = int(rec["button_index"])
    except (TypeError, ValueError):
        return None
    base = _field("town", "town_screen_layout", "button_navigation_pos", game_dir)
    start = _field("navigation", "building_navigation_layout",
                   "button_start_position", game_dir)
    spacing = _field("navigation", "building_navigation_layout",
                     "button_spacing", game_dir)
    size = _field("navigation", "building_navigation_layout", "base_size", game_dir)
    if spacing is None or size is None:
        return None
    # The strip is `base_size` wide (128) and each button is `button_spacing`
    # tall (68); the click goes to the middle of the cell in both axes, which
    # is the only part of it guaranteed to be inside the sprite at any scale.
    return _pt(base, start,
               (spacing[0] * index, spacing[1] * index),
               (size[0] / 2.0, spacing[1] / 2.0))


# Half a close glyph. `close_pos` anchors its TOP-LEFT, and clicking a corner
# is how a click misses a button that the log then says was pressed.
#
# Established, not guessed, from the one close button this repo has measured
# independently. `bridge/layout.py::TOWN_CLOSE_PANEL` was isolated by HUE off
# `captures/screens/town.png` at (0.7874, 0.1467) = design **(1511.8, 158.4)**,
# and it is the ACTIVITY LOG's X. That panel sits at
# `town_screen_layout.activity_log_pos (144, 132)` and its own
# `activity_log_layout.close_pos` is `(1352, 12)`, composing to **(1496, 144)**
# -- and `building_base_layout.close_pos` is **also (1496, 144)**, so DD draws
# every panel's X at one screen point. The difference between the composed
# anchor and the measured centroid is this offset, and the fact that a
# THREE-term composition lands on a hue measurement to within half a glyph is
# the strongest evidence in this file that the offsets compose the way it
# reads them.
CLOSE_GLYPH_CENTRE = (15.8, 14.4)


def building_close(game_dir: Path = GAME_DIR):
    """The X on any building panel, at the glyph's CENTRE.

    One point for all ten -- `building.layout` is the BASE layout every
    building interior inherits -- and the same point the activity log's X
    occupies, which is what let the centre offset be established at all.
    """
    return _pt(_field("building", "building_base_layout", "close_pos", game_dir),
               CLOSE_GLYPH_CENTRE)


def derived_panel_close(game_dir: Path = GAME_DIR):
    """Where `activity_log.layout` + `town.layout` say the activity log's X is,
    composed the long way.

    **Not used to click anything** -- `layout.TOWN_CLOSE_PANEL` is. This is the
    third cross-check, and the only one that exercises a THREE-term
    composition (screen -> panel -> widget), which is the rule every accessor
    in this file depends on and the other two checks do not test.
    """
    return _pt(_field("town", "town_screen_layout", "activity_log_pos", game_dir),
               _field("activity_log", "activity_log_layout", "close_pos", game_dir),
               CLOSE_GLYPH_CENTRE)


# Is the derived upgrade-tree geometry trustworthy? NO, and not because the
# arithmetic is wrong -- because **no upgrade tree is drawn on the stage coach
# panel at all**. Overlaid on `captures/screens/stage_coach_panel.png` the
# derived nodes land in the panel's dark left margin beside the navigation
# strip, and the panel body is the coachman and the recruit list with no tree
# anywhere. Either the trees need the panel scrolled, or this build puts them
# somewhere the layout file's `upgrade_base_pos` does not describe.
#
# So purchases are GATED. `upgrade_node` still computes the point -- the
# derivation is worth keeping and re-testing once a frame shows a tree -- and
# `UPGRADES_MEASURED` is what any caller must check first.
UPGRADES_MEASURED = True

# Where a tree's FIRST purchasable node sits, relative to the point composed
# from `upgrade_base_pos + upgrade_trees_offset + icon_offset +
# requirement_start_offset`. Measured, like `CLOSE_GLYPH_CENTRE` and
# `RECRUIT_PORTRAIT_OFFSET`, because the layout file does not say it.
#
# **The trees are not drawn until the building CREST is clicked**, which is why
# they were absent from the first banked panel and why purchases were gated for
# a day. Fran opened the Hamlet and let the bridge click the crest; the trees
# appeared, and `captures/stage_coach_upgrades.png` is that frame.
#
# What it confirms, before the offset is even applied:
#
#   node pitch   47.7 px of 1280  =  71.6 design   vs `requirement_spacing` 70
#   tree pitch  106.5 px of 1280  = 159.8 design   vs `upgrade_trees_spacing` 160
#   node counts        5, 5, 3                     vs numrecruits 5, rostersize 5,
#                                                     upgraded_recruits 3
#
# Both pitches and all three rank counts are the shipped values, so the
# derivation was right about everything except where the row starts -- and the
# tree titles read "Stagecoach Network", "Hero Barracks" and "Experienced
# Recruits" in exactly the file's order, with the first node priced 3 deed +
# 4 crest, which is `numrecruits.a` to the unit.
#
# The residual: first node at source x 224 against a derived 135, and row
# centre at source y 325.5 against 302.
UPGRADE_NODE_OFFSET = (133.5, 35.3)

# The building CREST -- the emblem beside the panel title, which carries a red
# banner when an upgrade is affordable. **The strongest candidate for what
# opens the upgrade view**, and therefore for why no tree is drawn until it is
# clicked.
#
# Measured off `captures/screens/stage_coach_panel.png`, brightened: the crest
# centres on frame (147.5, 170) at 1280x720, which is design (221, 255).
# `building_base_layout.upgrade_base_pos` is **(172, 259)** -- the **y agrees
# to 4 design px** and the x sits about half a crest to its left, exactly the
# top-left-anchor relationship `CLOSE_GLYPH_CENTRE` established for the close
# glyph. So `upgrade_base_pos` is not describing a tree that is missing; it is
# anchoring the crest that reveals one.
#
# UNTESTED. It is a hypothesis with a measurement behind it, which is why it
# is named and not wired: `plan_buy_upgrade` still refuses on
# `UPGRADES_MEASURED`. The experiment is one click and one frame.
BUILDING_CREST_OFFSET = (49.0, -4.0)


def building_crest(game_dir: Path = GAME_DIR):
    """The crest beside a building panel's title. See `BUILDING_CREST_OFFSET`."""
    return _pt(_field("building", "building_base_layout",
                      "upgrade_base_pos", game_dir),
               BUILDING_CREST_OFFSET)


def upgrade_node(tree: int, node: int, game_dir: Path = GAME_DIR):
    """One purchasable node: `tree` counts trees down the panel, `node` counts
    ranks across a tree, both 0-based.

    Verified against a live panel -- see `UPGRADE_NODE_OFFSET`. The stage
    coach's three trees are `numrecruits`, `rostersize` and
    `upgraded_recruits` in file order, drawn as "Stagecoach Network", "Hero
    Barracks" and "Experienced Recruits", with 5, 5 and 3 ranks.

    **The trees only exist once the building crest has been clicked**
    (`building_crest`); on a freshly opened panel there is nothing here to
    hit.
    """
    tree_gap = _field("building", "building_base_upgrade_layout",
                      "upgrade_trees_spacing", game_dir)
    node_gap = _field("building", "building_base_upgrade_tree_layout",
                      "requirement_spacing", game_dir)
    if (tree_gap is None or node_gap is None
            or not _index_ok(tree) or not _index_ok(node)):
        return None
    return _pt(
        _field("building", "building_base_layout", "upgrade_base_pos", game_dir),
        _field("building", "building_base_upgrade_layout",
               "upgrade_trees_offset", game_dir),
        _field("building", "building_base_upgrade_tree_layout",
               "icon_offset", game_dir),
        _field("building", "building_base_upgrade_tree_layout",
               "requirement_start_offset", game_dir),
        UPGRADE_NODE_OFFSET,
        (tree_gap[0] * tree, tree_gap[1] * tree),
        (node_gap[0] * node, node_gap[1] * node))


# -- the stage coach ------------------------------------------------------

# `hero_recruit_store.store_item_pos` anchors a recruit ROW, and the portrait
# -- which is what a drag has to grab -- sits down and right of that corner.
# The file names no offset for it, so it is MEASURED off
# `captures/screens/stage_coach_panel.png`: at 1280x720 the derived points
# landed at (697,121) and (697,188) while Murdac's and Fresle's portraits
# centre on (723,148) and (723,213) -- a consistent (+26,+26), which is
# (+39,+39) in the design space and about half a portrait.
#
# Equal in both axes because it is half of a square icon, which is also why
# one constant serves every row.
RECRUIT_PORTRAIT_OFFSET = (39.0, 39.0)

# The same correction serves the party bar and the roster list, and that it
# does is the check that it is half an icon rather than a fudge for one panel.
# Re-measured on the estate map (2026-09-01, 1280x720):
#
#   party slot 2   derived (641, 591)   portrait centres on (668, 617)
#   roster row 0   derived (1047, 94)   portrait centres on (1070, 119)
#
# Both are within 4 design px of (+39, +39) -- comfortably inside a portrait
# about 75 design px across -- so one constant covers all three.
PORTRAIT_CENTRE_OFFSET = RECRUIT_PORTRAIT_OFFSET


def _centred(point):
    """A slot's top-left corner -> the centre of the portrait drawn in it."""
    if point is None:
        return None
    return (point[0] + PORTRAIT_CENTRE_OFFSET[0] / 1920.0,
            point[1] + PORTRAIT_CENTRE_OFFSET[1] / 1080.0)


def party_portrait(index: int, game_dir: Path = GAME_DIR):
    """The party slot's portrait -- what a drag GRABS or drops onto."""
    return _centred(party_slot(index, game_dir))


def roster_portrait(index: int, game_dir: Path = GAME_DIR):
    """The roster row's portrait -- what a drag GRABS or drops onto."""
    return _centred(roster_slot(index, game_dir))


def recruit_slot(index: int, game_dir: Path = GAME_DIR):
    """The n-th hero on offer, in `town_state.read_recruits` order.

    The save lists them in the order the panel draws them, which is what makes
    an index enough -- `Recruit.slot` is the save's own key and this is its
    position on screen. The point is the PORTRAIT, because that is what a drag
    grabs; see `RECRUIT_PORTRAIT_OFFSET`.
    """
    gap = _field("stage_coach", "hero_recruit_store", "store_item_spacing", game_dir)
    if gap is None or not _index_ok(index):
        return None
    return _pt(
        _field("building", "building_base_layout", "body_base_pos", game_dir),
        _field("stage_coach", "hero_recruit_store", "store_item_pos", game_dir),
        RECRUIT_PORTRAIT_OFFSET,
        (gap[0] * index, gap[1] * index))


def roster_slot(index: int, game_dir: Path = GAME_DIR):
    """The n-th visible hero in the right-hand roster list -- the DROP TARGET a
    recruit is dragged onto.

    `element_max` is 8: the list scrolls, so an index past that is not on
    screen and this returns None rather than a point below the panel.
    """
    rec = _blocks("roster", str(game_dir)).get("town_roster_list_layout") or {}
    try:
        if not _index_ok(index, int(rec.get("element_max", 8))):
            return None
    except (TypeError, ValueError):
        return None
    gap = _field("roster", "town_roster_list_layout", "element_spacing", game_dir)
    if gap is None:
        return None
    # The portrait is what a drag grabs, not the row's top-left corner.
    return _pt(
        _field("town", "town_screen_layout", "roster_list_pos", game_dir),
        _field("roster", "town_roster_list_layout", "element_pos", game_dir),
        _field("roster", "town_roster_element_layout",
               "portrait_icon_offset", game_dir),
        (gap[0] * index, gap[1] * index))


# -- embarking ------------------------------------------------------------

def party_slot(index: int, game_dir: Path = GAME_DIR):
    """One of the four party slots on the town screen, index 0 LEFTMOST.

    **Index 0 is the BACK rank, not the front.** The bar mirrors the combat
    screen, where the party faces right and rank 1 is the hero nearest the
    enemies -- so rank 1 is the RIGHTMOST slot, index 3. Observed on the
    tutorial party (2026-09-01): Reynauld, who fights at rank 1, sat at index
    3 and Dismas at rank 2 sat at index 2, with indices 0 and 1 empty.

    That matters because `embark.py` returns a party in rank order, and
    dropping it into indices 0..3 in that order would embark the formation
    REVERSED. Use `party_slot_for_rank`.
    """
    if not _index_ok(index, 4):
        return None
    gap = _field("party", "embark_party_layout", "hero_slot_spacing", game_dir)
    if gap is None:
        return None
    return _pt(
        _field("town", "town_screen_layout", "embark_party_pos", game_dir),
        _field("party", "embark_party_layout", "hero_slot_start_offset", game_dir),
        (gap[0] * index, gap[1] * index))


# -- the provisioner ------------------------------------------------------

def _grid(bg_block: str, grid_block: str, index: int, game_dir: Path):
    rec = _blocks("provision", str(game_dir)).get(grid_block) or {}
    off = _field("provision", grid_block, "offset", game_dir)
    try:
        cols = int(rec.get("number_of_columns", 0))
    except (TypeError, ValueError):
        return None
    if off is None or cols <= 0 or not _index_ok(index):
        return None
    col, row = index % cols, index // cols
    return _pt(_field("provision", bg_block, "pos", game_dir),
               _field("provision", grid_block, "start_pos", game_dir),
               (off[0] * col, off[1] * row))


def shop_slot(index: int, game_dir: Path = GAME_DIR):
    """A cell in the provisioner's STOCK grid, row-major. 7 columns."""
    return _grid("provision_store_background_layout",
                 "provision_store_grid_layout", index, game_dir)


def party_pack_slot(index: int, game_dir: Path = GAME_DIR):
    """A cell in the party's pack on the PROVISION screen, row-major. 8 columns.

    Not the same grid as `layout.INVENTORY_X/Y`, which is the RAID pack drawn
    in a dungeon -- the two have different row pitches (160 design px here
    against ~154.5 there) and confusing them puts a discard on the wrong row.
    """
    return _grid("provision_party_background_layout",
                 "provision_party_grid_layout", index, game_dir)


# -- the heirloom exchange ------------------------------------------------

# The heirloom exchange TOGGLE on the town screen -- the two-arrow glyph at
# the right of the currency bar, past gold / bust / portrait / deed / crest /
# shard. **Nothing opens the exchange without it**, and the derived points
# below all assume an open panel.
#
# This is the SAME defect the navigation strip had, caught offline this time
# instead of costing a live run: `town_screen_layout.heirloom_exchange_pos` is
# `340 708` -> frame (227, 472), which is where the PANEL is drawn, while the
# button that opens it sits at (502, 674). The first live town visit clicked
# `exchange_from(0)` at (279, 545) on a bare Hamlet and the wallet, correctly,
# did not move.
#
# Measured off `captures/screens/hamlet.png` at 1280x720: the glyph centres on
# (502.5, 674). The same frame is where the deed icon is visibly a SCROLL,
# which is the picture behind "Crests for Scrolls".
HEIRLOOM_EXCHANGE_BUTTON = (502.5 / 1280.0, 674.0 / 720.0)

# **The from-selector is TWO SPINNERS, not four heirlooms, and that is what
# unblocked this** (2026-09-01). `choice_spacing 44 0` reads like a horizontal
# row of four choices, so `exchange_from(i)` was written as "the i-th
# heirloom" and the panel refused to make sense: it draws TWO up arrows, ONE
# down arrow and a single icon. Live, at 1280x720, the two up arrows sit
# 29.1 px apart -- 43.7 design px, i.e. exactly one `choice_spacing`. So there
# are two choices and they are:
#
#   choice 0   the heirloom TYPE      icon + up/down arrows
#   choice 1   HOW MANY of it to give  number + up/down arrows
#
# and the to-column then shows what that many buys of each OTHER type, one
# row each, with its own confirm button.
#
# **The cycle is five long and it starts on bust.** Fran: "the order is
# cycling, busts, portraits, deeds, crests and shards and busts again if you
# keep looping, when you click the trade heirlooms button you always appear on
# the busts." Measured the same, independently: clicking the type-up arrow
# walked bust -> portrait -> deed -> crest -> shard -> bust, identified by the
# mean colour of the drawn icon against the currency bar's own icons. The
# shard is a real spinner position with NO rates -- its count reads 0 and no
# confirm is offered -- which is why `EXCHANGE_CYCLE` is not just `HEIRLOOMS`.
#
# **The count starts at the type's CHEAPEST rate**, verified on all four:
# bust 2, portrait 1, deed 2, crest 3, against `min(from_amount)` over that
# type's rows in `heirloom_exchange.json` -- 2, 1, 2, 3. So how many arrow
# clicks a trade needs is derived rather than counted off the screen.
#
# The geometry then lands where the shipped file says, once each widget's
# anchor is established (the rule `CLOSE_GLYPH_CENTRE` already follows):
#
#   confirm      derived (472.0, 593.9) vs measured (471.0, 593.8) -- 1 px
#   type arrows  x derived 279.3 vs measured 278.8; y within 5 px of a
#                20-px glyph once half the shipped 32x30 arrow is added
#   count arrows x derived 308.7 vs measured 307.9
#   to rows      30.0 px pitch measured against 29.3 derived (44 design)
#
# **Screen recognition still cannot see this panel.** `SIG_BOX` is the centre
# block and the panel is bottom left, so `recognise` returns `hamlet` with it
# open. `bridge/town.py::_exchange_open` is the pixel test that replaces it,
# and it is one-sided the same way `_panel_is_open` is.
EXCHANGE_MEASURED = True

# The spinner's order, and the fifth position is not an heirloom you can
# trade. `HEIRLOOMS` is the wallet's own order and the exchange file's; the
# shard rides on the end with no rates at all.
EXCHANGE_CYCLE = ("bust", "portrait", "deed", "crest", "shard")
# `heirloom_exchange.arrow_up.png` is 32x30 design px. The arrows are centred
# on their anchor in x and hang BELOW it in y, so the clickable centre is half
# a glyph down -- established by measurement, not assumed, exactly as
# `CLOSE_GLYPH_CENTRE` was.
EXCHANGE_ARROW_SIZE = (32.0, 30.0)
# Which spinner is which.
EXCHANGE_TYPE_CHOICE = 0
EXCHANGE_COUNT_CHOICE = 1
# How many to-rows the panel draws: every heirloom except the one being given.
EXCHANGE_TO_ROWS = len(HEIRLOOMS) - 1
# **Where the icons are DRAWN, not where they are anchored.** The shipped
# `icon_offset`/`heirloom_icon_offset` are the sprite's corner and the sprite
# is a 72x144 sheet scaled to fit its slot, so half its drawn height is not
# recoverable from the file. These two corrections are measured, at 1280x720:
# the from-icon centres 10 px below its anchor and the to-icons 5 px above
# theirs -- the two sides genuinely differ, which is why one constant does not
# serve both.
#
# It matters because these points are SAMPLED rather than clicked, and a
# window half off the glyph reads half the glyph: with the anchor used raw,
# the crest matched `bust` at a distance of 35 and a margin of 1.2, which is
# the recogniser correctly saying it could not tell.
EXCHANGE_FROM_ICON_CENTRE = (0.0, 16.0)
EXCHANGE_TO_ICON_CENTRE = (0.0, -8.0)


def exchange_button():
    """Where to click on the town screen to open the heirloom exchange."""
    return HEIRLOOM_EXCHANGE_BUTTON


def exchange_from(index: int, game_dir: Path = GAME_DIR):
    """The n-th heirloom to trade AWAY, along the top row of the widget."""
    gap = _field("exchange", "heirloom_exchange_heirloom_from_layout",
                 "choice_spacing", game_dir)
    if gap is None or not _index_ok(index):
        return None
    return _pt(
        _field("town", "town_screen_layout", "heirloom_exchange_pos", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_from_layout",
               "choice_start_offset", game_dir),
        (gap[0] * index, gap[1] * index))


def exchange_amount(index: int, up: bool, game_dir: Path = GAME_DIR):
    """The up/down arrow of the n-th from-SPINNER: 0 is the type, 1 the count.

    Not "the n-th heirloom" -- see the note above `EXCHANGE_MEASURED`. The
    shipped offset is the glyph's top edge and the glyph is 30 design px tall,
    so half of it is added to reach something clickable.
    """
    gap = _field("exchange", "heirloom_exchange_heirloom_from_layout",
                 "choice_spacing", game_dir)
    if gap is None or not _index_ok(index):
        return None
    return _pt(
        _field("town", "town_screen_layout", "heirloom_exchange_pos", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_from_layout",
               "choice_start_offset", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_from_layout",
               "arrow_up_offset" if up else "arrow_down_offset", game_dir),
        (0.0, EXCHANGE_ARROW_SIZE[1] / 2.0),
        (gap[0] * index, gap[1] * index))


def exchange_type_arrow(up: bool = True, game_dir: Path = GAME_DIR):
    """The arrow that cycles WHICH heirloom is being given away."""
    return exchange_amount(EXCHANGE_TYPE_CHOICE, up, game_dir)


def exchange_count_arrow(up: bool = True, game_dir: Path = GAME_DIR):
    """The arrow that changes HOW MANY of it are being given away."""
    return exchange_amount(EXCHANGE_COUNT_CHOICE, up, game_dir)


def exchange_from_icon(game_dir: Path = GAME_DIR):
    """Where the given-away heirloom's icon is drawn -- what to look AT.

    The only thing in the panel that is read rather than clicked: it is how
    `bridge/town.py` checks the spinner is where it thinks before confirming
    anything.
    """
    return _pt(
        _field("town", "town_screen_layout", "heirloom_exchange_pos", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_from_layout",
               "choice_start_offset", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_from_layout",
               "icon_offset", game_dir),
        EXCHANGE_FROM_ICON_CENTRE)


def exchange_to_icon(index: int, game_dir: Path = GAME_DIR):
    """Where the n-th received heirloom's icon is drawn."""
    gap = _field("exchange", "heirloom_exchange_heirloom_to_layout",
                 "choice_spacing", game_dir)
    if gap is None or not _index_ok(index, EXCHANGE_TO_ROWS):
        return None
    return _pt(
        _field("town", "town_screen_layout", "heirloom_exchange_pos", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_to_layout",
               "choice_start_offset", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_to_layout",
               "heirloom_icon_offset", game_dir),
        EXCHANGE_TO_ICON_CENTRE,
        (gap[0] * index, gap[1] * index))


def exchange_to_row(from_type: str, to_type: str) -> Optional[int]:
    """Which to-row carries `to_type` when `from_type` is being given.

    The panel lists every heirloom except the one being given, in
    `HEIRLOOMS` order -- measured on all four spinner positions, and the same
    order the wallet and the rate file use.
    """
    if from_type not in HEIRLOOMS or to_type not in HEIRLOOMS:
        return None
    rows = [h for h in HEIRLOOMS if h != from_type]
    return rows.index(to_type) if to_type in rows else None


def exchange_to(index: int, game_dir: Path = GAME_DIR):
    """The n-th heirloom to receive, down the right-hand column."""
    gap = _field("exchange", "heirloom_exchange_heirloom_to_layout",
                 "choice_spacing", game_dir)
    if gap is None or not _index_ok(index):
        return None
    return _pt(
        _field("town", "town_screen_layout", "heirloom_exchange_pos", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_to_layout",
               "choice_start_offset", game_dir),
        (gap[0] * index, gap[1] * index))


def exchange_confirm(index: int = 0, game_dir: Path = GAME_DIR):
    """The confirm button beside the n-th to-choice. It is per-row, not shared:
    `confirm_offset` lives inside the to-layout and rides its spacing."""
    gap = _field("exchange", "heirloom_exchange_heirloom_to_layout",
                 "choice_spacing", game_dir)
    if gap is None or not _index_ok(index):
        return None
    return _pt(
        _field("town", "town_screen_layout", "heirloom_exchange_pos", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_to_layout",
               "choice_start_offset", game_dir),
        _field("exchange", "heirloom_exchange_heirloom_to_layout",
               "confirm_offset", game_dir),
        (gap[0] * index, gap[1] * index))


# -- the cross-check ------------------------------------------------------

def derived_modal_answer(index: int = 0, game_dir: Path = GAME_DIR):
    """Where `shared/confirm_dialog` says the n-th answer button sits.

    **This is not used to click anything** -- `layout.MODAL_OK` is, and it was
    measured. This exists so `tests/bridge/test_townlayout.py` can compare the two and
    fail if the derivation and the measurement ever stop agreeing, which is the
    only evidence that everything else in this file is being read correctly.
    Answer buttons are `button_size` tall and anchored at their top, so the
    clickable centre is half a button down.
    """
    gap = _field("confirm", "confirm_dialog_answers_layout", "spacing", game_dir)
    size = _field("confirm", "confirm_dialog_answer_layout", "button_size", game_dir)
    if gap is None or size is None or not _index_ok(index):
        return None
    return _pt(
        _field("confirm", "confirm_dialog_layout", "base_pos", game_dir),
        _field("confirm", "confirm_dialog_layout", "answers_offset", game_dir),
        _field("confirm", "confirm_dialog_answers_layout", "start_offset", game_dir),
        (gap[0] * index, gap[1] * index),
        (0.0, size[1] / 2.0))


def derived_item_pitch(game_dir: Path = GAME_DIR):
    """`(column pitch, row pitch)` for an inventory grid, in DESIGN px.

    The column pitch is stated twice by the game -- `icon_size` plus its
    gutter, and `provision_store_grid_layout.offset` -- and the raid pack was
    edge-detected at exactly that. See the module docstring.
    """
    icon = _field("inventory", "inventory_item_layout", "icon_size", game_dir)
    off = _field("provision", "provision_store_grid_layout", "offset", game_dir)
    if icon is None or off is None:
        return None
    return (off[0], icon[1])


def main() -> int:
    """`python bridge/townlayout.py` -- print every derived point, so the
    derivation can be eyeballed against a frame without running the game."""
    def show(label, pt):
        print(f"  {label:22s} "
              + ("-" if pt is None else f"{pt[0]:.4f} {pt[1]:.4f}"))

    print(f"design space {DESIGN_W:.0f}x{DESIGN_H:.0f}\n")
    for name in BUILDINGS:
        show(name, building_button(name))
    print()
    show("close panel", building_close())
    show("derived log close", derived_panel_close())
    show("upgrade tree0 node0", upgrade_node(0, 0))
    show("upgrade tree0 node1", upgrade_node(0, 1))
    show("upgrade tree1 node0", upgrade_node(1, 0))
    show("recruit 0", recruit_slot(0))
    show("roster 0", roster_slot(0))
    for i in range(4):
        show(f"party slot {i}", party_slot(i))
    show("shop 0", shop_slot(0))
    show("party pack 0", party_pack_slot(0))
    show("exchange from 0", exchange_from(0))
    show("exchange to 0", exchange_to(0))
    show("exchange confirm 0", exchange_confirm(0))
    print("\n  CROSS-CHECK")
    modal = derived_modal_answer()
    print(f"    derived modal answer 0  "
          f"{'-' if modal is None else f'{modal[0] * DESIGN_W:.1f} {modal[1] * DESIGN_H:.1f} design px'}")
    print(f"    measured MODAL_OK       946.5 584.2 design px")
    print(f"    derived item pitch      {derived_item_pitch()} design px")
    print(f"    measured pack pitch     (80.0, 154.5) design px")
    log = derived_panel_close()
    print(f"    derived log close       "
          f"{'-' if log is None else f'{log[0]:.4f} {log[1]:.4f}'}")
    print(f"    measured TOWN_CLOSE     0.7874 0.1467")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def party_slot_for_rank(rank: int, game_dir: Path = GAME_DIR):
    """The party slot holding combat RANK `rank` (1 = front), as a portrait.

    `party_slot` is indexed left to right and the front rank is on the right,
    so this is the only accessor an embark should use.
    """
    if not isinstance(rank, int) or isinstance(rank, bool) or not 1 <= rank <= 4:
        return None
    return party_portrait(4 - rank, game_dir)


# -- the estate map -------------------------------------------------------
#
# Region banners are SCENE geometry, like the Hamlet's buildings: the shipped
# layout files index building buttons and panel widgets, and say nothing about
# where a region sits on the painted map. So these are MEASURED, and -- as
# with `BUILDING_SCENE_POINTS` -- an unmeasured region is refused rather than
# guessed at.
#
# Two points per region, because they do different things: the BANNER is the
# name plate (hovering it names the quest), and the EMBLEM is the quest token
# on the map, which is what SELECTS the quest. Clicking the banner alone left
# the previous quest selected.
#
# Measured 2026-09-01 at 1280x720 on the estate map.
REGION_SCENE_POINTS = {
    "darkest_dungeon": {"banner": (918.0 / 1280.0, 75.0 / 720.0),
                        "emblem": (845.0 / 1280.0, 125.0 / 720.0)},
}


def region_point(name: str, which: str = "emblem"):
    """The estate map's point for a region, or None if it was never measured.

    Refusing is the point: a wrong point on this screen selects a DIFFERENT
    quest, and an embark is not something to discover by doing.
    """
    return (REGION_SCENE_POINTS.get(name) or {}).get(which)


# -- the quest tokens on the estate map -----------------------------------
#
# **This one IS shipped geometry**, unlike the region banners above, and it
# supersedes `REGION_SCENE_POINTS` for the thing that matters -- selecting a
# quest. `quest_select.layout.darkest` gives every dungeon its own block with
# a `quest_map_pos`, and `town_quest_select_dungeon_layout` gives the offset
# from there to the first quest button:
#
#   crypts 940 220   warrens 815 400   weald 1000 525   cove 1260 400
#   darkestdungeon 1260 100   town 1260 750   courtyard 570 330 (DLC file)
#   quest_button_start_offset 160 92   quest_button_spacing 76 80
#
# Matched against a live estate map with `tools/find_button.py` on the game's
# own quest-type icons, at 1280x720:
#
#   dungeon          derived + (8, 92)     measured
#   crypts           (948, 312)            (948.1, 312.0)
#   darkestdungeon   (1268, 192)           (1267.6, 192.0)
#   courtyard        (578, 422)            (578.3, 422.3)
#
# Three dungeons, two different layout files, both axes inside 0.5 design px.
# The **y is the file's own 92 verbatim**; only the x carries a measured
# residual, and it is 8 rather than the file's 160 -- the same shape of
# derived-plus-one-measured-constant as `UPGRADE_NODE_OFFSET` and
# `CLOSE_GLYPH_CENTRE`.
QUEST_BUTTON_OFFSET = (8.0, 92.0)

# **A SECOND quest in one region is UNCONFIRMED geometry.** Every live map so
# far has held at most one quest per dungeon, so `quest_button_spacing 76 80`
# has never been observed. Two readings fit the single point equally well: the
# row starts at the offset and runs right, or the row is CENTRED on it, which
# for one quest is the same pixel and for two is not. Guessing costs a
# different quest than the one that was planned, so `quest_button` refuses any
# index but 0 until a map with two shows up and settles it.
QUEST_ROW_MEASURED = False


def quest_map_pos(dungeon: str, game_dir: Path = GAME_DIR):
    """A dungeon's anchor on the estate map, in DESIGN space (not fractions).

    Base file first, then the Crimson Court's -- the courtyard is the only
    dungeon the DLC adds and it lives in its own file.
    """
    block = f"quest_select_dungeon_layout_{dungeon}"
    for key in ("quest_select", "quest_select_cc"):
        point = _field(key, block, "quest_map_pos", game_dir)
        if point is not None:
            return point
    return None


def quest_button(dungeon: str, index: int = 0, game_dir: Path = GAME_DIR):
    """The quest token to CLICK for `dungeon`, as client-rect fractions.

    This is what selects a quest; the banner beside it only names one. See
    `QUEST_BUTTON_OFFSET` for the three-dungeon cross-check, and
    `QUEST_ROW_MEASURED` for why a second quest in one region refuses.
    """
    if not _index_ok(index):
        return None
    if index and not QUEST_ROW_MEASURED:
        return None
    gap = _field("quest_select", "town_quest_select_dungeon_layout",
                 "quest_button_spacing", game_dir)
    row = _field("quest_select", "town_quest_select_dungeon_layout",
                 "quest_button_start_offset", game_dir)
    if gap is None or row is None:          # the file is what says 4 per row
        return None
    per_row = _blocks("quest_select", str(game_dir)).get(
        f"quest_select_dungeon_layout_{dungeon}") or {}
    try:
        cols = int(per_row.get("quest_number_of_quests_in_row", 4)) or 4
    except (TypeError, ValueError):
        cols = 4
    col, line = index % cols, index // cols
    return _pt(quest_map_pos(dungeon, game_dir), QUEST_BUTTON_OFFSET,
               (gap[0] * col, gap[1] * line))


def quest_point(quest, game_dir: Path = GAME_DIR):
    """`quest_button` for a `town_state.Quest`."""
    return quest_button(getattr(quest, "dungeon", ""), 0, game_dir)


# -- the provisioner's items ----------------------------------------------
#
# `shop_slot` gives a CELL; this gives the item drawn in it. The mapping is
# **derived, not measured**: `provision.json`'s
# `default_store_inventory_item_lists[length]` lists the stock in the order
# the panel draws it, and nine items into a 7-column grid puts torch at index
# 8 -- exactly where `bridge/layout.py::PROVISION_TORCH` was independently
# hand-clicked, with food at 0 where `PROVISION_FOOD` is. Two constants
# measured by a human, reproduced by a file nobody derived them from.
#
# The icon centre sits down and right of the cell corner, the same
# top-left-anchor relationship `RECRUIT_PORTRAIT_OFFSET` established: measured
# off `captures/screens/provision.png`, +(40, 72) design px.
ICON_CENTRE_OFFSET = (40.0, 72.0)


def shop_item_index(item: str, length: int = 1,
                    game_dir: Path = GAME_DIR) -> Optional[int]:
    """Which cell of the stock grid holds `item`, per the shipped store list."""
    from data import provisions

    order = provisions.store_order(length, str(game_dir))
    return order.index(item) if item in order else None


def shop_item(item: str, length: int = 1, game_dir: Path = GAME_DIR):
    """The provisioner's icon for `item` -- what a purchase CLICKS."""
    index = shop_item_index(item, length, game_dir)
    if index is None:
        return None
    cell = shop_slot(index, game_dir)
    if cell is None:
        return None
    return (cell[0] + ICON_CENTRE_OFFSET[0] / DESIGN_W,
            cell[1] + ICON_CENTRE_OFFSET[1] / DESIGN_H)


# -- scrolling the roster -------------------------------------------------
#
# `element_max` is 8 and a live roster is 12, so **the list arrives scrolled**
# and seating a hero from row 8+ is impossible without this.
#
# **THE ARROWS SIT ON THE ROW BAND'S EDGES, not on the list's frames**, and the
# frame reading is what the first version got wrong: composing
# `roster_list_pos + top/bottom_frame_offset + scroll_*_button_offset` puts
# them at design y **-88 and -22**, above the top of a 1080-tall screen. The
# band is `element_pos.y` (132) to `element_pos.y + element_spacing.y *
# element_max` (908), and each button sits **12 design px outside its own
# edge** -- 12 being `scroll_down_button_offset`'s own magnitude, the single
# shipped number the derivation needs.
#
# Measured on a live estate map, 2026-09-02, by hue-detecting the orange
# chevrons in the list's left gutter at 1280x720:
#
#   up chevron    frame (1053.6, 79.9)  -> design (1580.3, 119.8)  vs derived 120.0
#   down chevron  frame (1053.6, 613.1) -> design (1580.3, 919.7)  vs derived 920.0
#
# **-0.20 and -0.30 design px, from one shipped constant applied to both
# edges.** The two chevrons share an x to the pixel, which is the check that
# they are the same widget mirrored rather than two coincidences; only that x
# needs a measured residual, +10.3 over `roster_list_pos.x +
# top_frame_offset.x`.
#
# Each chevron is drawn only when it can do something -- the up one is absent
# at offset 0 and the down one at the bottom, which is why the first probe
# found one and not the other.
ROSTER_SCROLL_X_RESIDUAL = 10.3

# Flown 2026-09-02: four clicks walked a 12-hero list 4 -> 3 -> 2 -> 1 -> 0,
# with `town._roster_offset` reading each step back off the portraits. The
# reason this was False -- "a mis-scrolled list silently RE-INDEXES every row"
# -- stopped applying the moment the offset became measurable, and it turned
# out to be a problem the bridge already HAD rather than one scrolling would
# introduce: the list was sitting at offset 4 before anything touched it.
ROSTER_SCROLL_MEASURED = True


def roster_band(game_dir: Path = GAME_DIR):
    """`(top, bottom)` of the drawn row band, in DESIGN px, or None.

    The band is what the scroll arrows are anchored to and what
    `roster_slot`'s `element_max` bound describes, so it is derived once here
    rather than twice.
    """
    rec = _blocks("roster", str(game_dir)).get("town_roster_list_layout") or {}
    top = _field("roster", "town_roster_list_layout", "element_pos", game_dir)
    gap = _field("roster", "town_roster_list_layout", "element_spacing", game_dir)
    if top is None or gap is None:
        return None
    try:
        rows = int(rec.get("element_max", 8))
    except (TypeError, ValueError):
        return None
    return (top[1], top[1] + gap[1] * rows)


def roster_scroll(down: bool = True, game_dir: Path = GAME_DIR):
    """The roster list's scroll chevron. See the note above.

    The arrow is only DRAWN when it can act -- at the top of the list there is
    no up chevron -- so a click here is a no-op rather than a misclick when the
    list is already at the end. `bridge/town.py::scroll_roster_to` confirms the
    move off the portraits either way.
    """
    band = roster_band(game_dir)
    step = _field("roster", "town_roster_list_layout",
                  "scroll_down_button_offset", game_dir)
    left = _field("town", "town_screen_layout", "roster_list_pos", game_dir)
    frame = _field("roster", "town_roster_list_layout", "top_frame_offset", game_dir)
    if band is None or step is None or left is None or frame is None:
        return None
    out = abs(step[1])
    y = band[1] + out if down else band[0] - out
    return design_pt(left[0] + frame[0] + ROSTER_SCROLL_X_RESIDUAL, y)


# -- the guild and the blacksmith -----------------------------------------
#
# Both are ordinary building panels, so the anchor is `body_base_pos` and the
# rows come from the building's own layout file:
#
#   guild        skill_pos 44 -4     skill_spacing 0 91      (7 skills)
#   blacksmith   equipment_pos 50 20 equipment_spacing 0 176 (weapon, armour)
#
# and a rank across a row is `requirement_spacing 75 0` in each file's own
# `*_upgrade_tree_layout`, which is the same shape as `upgrade_node`.
#
# **UNMEASURED, and the stage coach is the reason to say so out loud**: its
# derived tree points landed in a margin until a live frame moved them by
# (133.5, 35.3), and the trees were not drawn at all until the building crest
# was clicked. Expect a residual here too. `bridge/town.py` refuses to click
# either until a frame of the panel is banked; un-gating is one visit.
GUILD_MEASURED = False

# The Guild draws one row per skill and the hero carries seven.
GUILD_SKILL_ROWS = 7


def guild_skill_node(skill: int, rank: int = 0, game_dir: Path = GAME_DIR):
    """The Guild's buy button for one skill at one rank, both 0-based.

    `skill` counts rows down the panel in the hero's own skill order (the same
    order `data/town_costs.py` reads out of `<class>.upgrades.json`); `rank` 0
    is the unlock, which is the only one this campaign can afford.
    """
    gap = _field("guild", "guild_layout", "skill_spacing", game_dir)
    node = _field("guild", "guild_upgrade_tree_layout",
                  "requirement_spacing", game_dir)
    if gap is None or node is None:
        return None
    if not _index_ok(skill, GUILD_SKILL_ROWS) or not _index_ok(rank):
        return None
    return _pt(
        _field("building", "building_base_layout", "body_base_pos", game_dir),
        _field("guild", "guild_layout", "skill_pos", game_dir),
        _field("guild", "guild_upgrade_tree_layout", "icon_offset", game_dir),
        _field("guild", "guild_upgrade_tree_layout",
               "requirement_start_offset", game_dir),
        (gap[0] * skill, gap[1] * skill),
        (node[0] * rank, node[1] * rank))


def blacksmith_node(row: int, rank: int = 0, game_dir: Path = GAME_DIR):
    """The Blacksmith's buy button: `row` 0 is the weapon, 1 the armour.

    Nothing buys these yet -- rank 1 is 750 gold but needs `blacksmith.weapon
    a` (8 deed + 8 crest, against a wallet holding 0 deed) and resolve 1, which
    two of the twelve live heroes have. It exists so the panel can be opened
    and REPORTED before it is ever trusted.
    """
    gap = _field("blacksmith", "blacksmith_layout", "equipment_spacing", game_dir)
    node = _field("blacksmith", "blacksmith_upgrade_tree_layout",
                  "requirement_spacing", game_dir)
    if gap is None or node is None:
        return None
    if not _index_ok(row, 2) or not _index_ok(rank):
        return None
    return _pt(
        _field("building", "building_base_layout", "body_base_pos", game_dir),
        _field("blacksmith", "blacksmith_layout", "equipment_pos", game_dir),
        _field("blacksmith", "blacksmith_upgrade_tree_layout",
               "icon_offset", game_dir),
        _field("blacksmith", "blacksmith_upgrade_tree_layout",
               "requirement_start_offset", game_dir),
        (gap[0] * row, gap[1] * row),
        (node[0] * rank, node[1] * rank))
