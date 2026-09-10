@echo off
rem ---------------------------------------------------------------------
rem  PLAY - the bridge takes the controls
rem  Arms all six dungeon layers. F9 stops it. Keep the game focused.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   *** THE BRIDGE WILL MOVE YOUR MOUSE AND PLAY THE GAME ***
echo.
echo   Keep Darkest Dungeon focused and in the foreground.
echo   Press F9 at any time to stop it, or Ctrl+C in this window.
echo.
pause
python bridge\run.py --act --survival 12 --log bat\play.log %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
