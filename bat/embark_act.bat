@echo off
rem ---------------------------------------------------------------------
rem  EMBARK - do it
rem  Scout the quests, seat a curated comp in its own formation, visit the
rem  guild, buy the basket and go. Every action is checked against the save.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   *** THIS CLICKS AND DRAGS IN THE GAME ***  Keep it focused.
echo.
pause
python bridge\town.py --embark-setup --act %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
