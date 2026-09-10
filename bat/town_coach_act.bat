@echo off
rem ---------------------------------------------------------------------
rem  STAGE COACH - do it
rem  Trade heirlooms, buy the upgrades, hire. Every action is checked against the save.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   *** THIS CLICKS AND DRAGS IN THE GAME ***  Keep it focused.
echo.
pause
python bridge\town.py --coach --act %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
