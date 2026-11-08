# Contracts for `campaign/`

The campaign simulator. The plan, scope and phases are `docs/CAMPAIGN_SIM.md`;
read it first.

- **Tables are data, draws are assumptions.** Every number comes from a game
  file through a `data/` loader (`data/quest_gen_loader.py` for the board).
  Every rule for USING a table that the files do not state is written in the
  code as `ASSUMPTION:` with what it was checked against. Never bury one in a
  constant.
- **The decide layers are the bridge's, unchanged.** The simulator builds a
  `bridge.town_state.TownState` and calls `bridge.townplan` /
  `bridge.embarkplan`. Do not fork them into here: the baseline must be
  exactly what the live player does, and an improvement found here must
  reach the live player for free.
- **Layering**: `campaign` may import `data`, `engine`, `env`, `bridge` and
  `eval_expedition` (`tests/suite/test_architecture.py`). Nothing below it imports it.
- **Calibration sources** are the saves (`profile_8` is Fran's own campaign)
  and the live raids in `bat/play.log`. A rule that contradicts a save is
  wrong until shown otherwise.
- **Nothing of Red Hook's is published from here**: no decompiled code, no
  copied data files. The simulator reads the reader's own install.
