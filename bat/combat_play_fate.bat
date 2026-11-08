@echo off
rem ---------------------------------------------------------------------
rem  PLAY WITH FATE - the bridge takes the controls AND reads the dice
rem  Same as combat_play.bat, plus --fate: every turn it reads the game's
rem  next hit/crit/damage rolls off the save (DECOMP.md F15) and shows them.
rem  F9 stops it. Keep the game focused.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   *** THE BRIDGE WILL MOVE YOUR MOUSE AND PLAY THE GAME ***
echo.
echo   Keep Darkest Dungeon focused and in the foreground.
echo   Press F9 at any time to stop it, or Ctrl+C in this window.
echo   Every advice block shows this turn's known dice (fate: ...).
echo.
pause
python bridge\run.py --act --survival 12 --fate --log bat\play.log %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
