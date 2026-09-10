@echo off
rem ---------------------------------------------------------------------
rem  RECRUIT ALL - hire everyone the coach is offering
rem  Drags each recruit onto the roster and checks the save grew.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   *** THIS DRAGS IN THE GAME ***  Keep it focused.
echo.
pause
python bridge\town.py --recruit-all --act %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
