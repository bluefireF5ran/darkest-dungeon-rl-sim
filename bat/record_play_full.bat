@echo off
rem ---------------------------------------------------------------------
rem  RECORD (FULL) - you play, it records how, AND what the game computed
rem  Same as record_play.bat, plus the game's own debug channel: every
rem  attack's hit chance, roll and damage draw, on the same clock as the
rem  saves. Sends NOTHING to the game. Used by tools\damage_oracle.py.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo Recording your play AND the game's debug channel. Nothing is sent to the game.
echo.
echo   Where : archive\recordings\^<date-time^>\   (events, frames, saves, trace.log)
echo   Mark  : press F8 in the game to flag a moment (you hear a beep).
echo   Note  : close DebugView or any debugger first - only one program can
echo           read the debug channel. If it cannot, recording goes on without it.
echo.
echo Fights are what this session is for: every hit you land is a measurement.
echo Stops a couple of minutes after the game closes, or press Ctrl+C here.
echo.
python bridge\recorder.py --trace %*
set RC=%ERRORLEVEL%
echo.
python tools\recording_report.py
echo.
python tools\damage_oracle.py
popd
echo.
echo [exit code %RC%]
pause
endlocal
