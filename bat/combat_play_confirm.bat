@echo off
rem ---------------------------------------------------------------------
rem  PLAY, ASKING FIRST - one combat turn at a time
rem  Same as combat_play, but it asks on this terminal before every combat turn.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   The bridge plays, but asks THIS WINDOW before each combat turn.
echo   The five out-of-combat layers still act without asking.
echo   An unanswered prompt STOPS the loop - answer it or press Ctrl+C.
echo.
pause
python bridge\run.py --act --confirm --survival 12 --log bat\play_confirm.log %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
