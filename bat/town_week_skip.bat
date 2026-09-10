@echo off
rem ---------------------------------------------------------------------
rem  WEEK SKIP - one hero into the Darkest Dungeon and straight back out
rem  Burns one hero's week (20 stress) to refresh the stage coach.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   *** THIS PLAYS A WHOLE EMBARK AND ABANDON ***  Keep the game focused.
echo   The hero it sends comes back with +20 stress. Default: the Vestal.
echo.
pause
python bridge\town.py --week-skip --act %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
