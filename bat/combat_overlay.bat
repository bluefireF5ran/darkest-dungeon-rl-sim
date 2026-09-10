@echo off
rem ---------------------------------------------------------------------
rem  OVERLAY - the advice drawn on top of the game
rem  A click-through HUD. Decides nothing and sends nothing.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo Drawing the advice over the game. Nothing is sent to Darkest Dungeon.
echo Close this window to stop.
echo.
python bridge\overlay.py --survival 12 --log bat\overlay.log %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
