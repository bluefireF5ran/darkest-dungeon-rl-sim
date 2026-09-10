# bat/ - double-click scripts

Each one changes to the repo root first, so they work wherever the shortcut
lives, and each ends in `pause` so a double-clicked window does not vanish
before you have read it. Anything after the filename on the command line is
passed straight through, so `combat_play.bat --model models/other.zip` works.

## Does it touch the game?

| script | sends input? |
|---|---|
| `where_am_i.bat` | no - one frame, one answer |
| `combat_advise.bat` | **no** - watches and prints what it would do |
| `combat_overlay.bat` | **no** - draws the same advice over the game |
| `town_coach.bat` | no - prints the town plan off the save |
| `embark_plan.bat` | no - prints the whole embark plan off the save |
| `combat_play_confirm.bat` | yes, but asks this window before each combat turn |
| `combat_play.bat` | **yes** - plays all six dungeon layers |
| `town_coach_act.bat` | **yes** - trades, buys and hires |
| `town_week_skip.bat` | **yes** - embarks one hero and abandons |
| `town_recruit_all.bat` | **yes** - hires everyone on offer |
| `embark_act.bat` | **yes** - seats a comp, provisions and embarks |
| `new_campaign.bat` | **yes** - makes a save and plays the tutorial |

**F9 stops anything that acts.** So does Ctrl+C in the window. Everything that
acts needs Darkest Dungeon focused and in the foreground, because the input
path moves your real cursor.

## The two combat ones

`combat_advise.bat` is the ruler and `combat_play.bat` is the player. They run
the identical decision stack -- same checkpoint, same six layers, same log
format -- and differ only in whether `--act` is passed. So a disagreement
between what advise prints and what play does is a bug, not a setting.

## Logs

**And each `--log` now writes a `.jsonl` beside it** — `bat/advise.jsonl`,
`bat/play.jsonl` — with one JSON object per event: every decision and the
board it was made on, every combat turn played, and every change to the
Hamlet (wallet, hires, treatment, upgrades, trinkets, the quest map). The
prose log is for reading; that file is for tooling. Both APPEND, so several
sittings accumulate into one corpus. Read it with
`python tools/journal_report.py`.

They write beside themselves: `bat/advise.log`, `bat/play.log`,
`bat/overlay.log`, `bat/boot.log`. Move one to `archive/logs/` if it is worth
keeping; they APPEND across runs of the same script.

## What is NOT here, on purpose

Nothing that deletes a save. Campaign slots are deleted by path, through
`bridge/reset.py`'s `OwnedSlot`, never by clicking the X in the save list --
so there is no `delete_save.bat` to mis-click.
