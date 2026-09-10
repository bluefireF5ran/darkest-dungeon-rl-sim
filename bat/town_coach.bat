@echo off
rem ---------------------------------------------------------------------
rem  STAGE COACH - print the plan, send nothing
rem  Trades, upgrades and hires it WOULD do, read off the save.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
python bridge\town.py --coach %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
