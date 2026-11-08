@echo off
rem ---------------------------------------------------------------------
rem  RECORD - you play, it records how
rem  Sends NOTHING to the game. Records your mouse and keyboard ONLY while
rem  Darkest Dungeon is the focused window, a small screenshot per press,
rem  and every save the game writes.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo Recording your play. Nothing is sent to Darkest Dungeon.
echo.
echo   Where : archive\recordings\^<date-time^>\
echo   What  : your clicks and keys while the game is focused (nothing
echo           typed in other windows), a screenshot per press, every save.
echo   Mark  : press F8 in the game to flag a moment worth looking at
echo           (you hear a beep when it is recorded).
echo   Frames: launch the game with Steam launch options  -size 1280 720
echo           for screenshots. Fullscreen still records clicks and saves.
echo.
echo Start the game whenever you like. Stops on its own a couple of minutes
echo after the game closes, or press Ctrl+C in this window.
echo.
python bridge\recorder.py %*
set RC=%ERRORLEVEL%
echo.
python tools\recording_report.py
popd
echo.
echo [exit code %RC%]
pause
endlocal
