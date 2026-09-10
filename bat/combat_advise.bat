@echo off
rem ---------------------------------------------------------------------
rem  ADVISE ONLY - the bridge watches and says what to do
rem  Sends NOTHING to the game. Read the terminal and play it yourself.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo Watching the game and printing advice. Nothing is sent to Darkest Dungeon.
echo.
echo   Readable log   : bat\advise.log
echo   Structured data: bat\advise.jsonl
echo                    every decision, the board it was made on, and every
echo                    change to the Hamlet. Both files APPEND, so play as
echo                    many sessions as you like.
echo.
echo Runs for up to 8 hours. Press Ctrl+C in this window to stop.
echo.
python bridge\run.py --survival 12 --log bat\advise.log --duration 28800 %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
