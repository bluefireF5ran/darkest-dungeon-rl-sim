@echo off
rem ---------------------------------------------------------------------
rem  WHERE AM I - which screen the bridge thinks is up
rem  One frame, one answer. Sends nothing. The first thing to run when something looks stuck.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
python bridge\screen.py --once %*
echo.
python bridge\town.py --where %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
