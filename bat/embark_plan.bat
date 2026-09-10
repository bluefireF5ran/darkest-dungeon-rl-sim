@echo off
rem ---------------------------------------------------------------------
rem  EMBARK - print the plan, send nothing
rem  Which quest, which curated comp, which formation, which skills to
rem  unlock and what to buy at the provisioner. Read off the save.
rem ---------------------------------------------------------------------
setlocal
pushd "%~dp0.."
python bridge\embarkplan.py %*
set RC=%ERRORLEVEL%
popd
echo.
echo [exit code %RC%]
pause
endlocal
