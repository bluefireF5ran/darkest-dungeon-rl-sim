@echo off
rem ---------------------------------------------------------------------
rem  NEW CAMPAIGN - front end to the end of the tutorial
rem  Creates a save slot and plays the Old Road. Existing saves are never touched.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
echo.
echo   *** THIS CREATES A NEW SAVE AND PLAYS THE TUTORIAL ***
echo   It uses the first FREE slot. Nothing existing is deleted.
echo   Start it from the title screen. F9 stops it.
echo.
pause
python bridge\run.py --boot --act --log bat\boot.log %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
