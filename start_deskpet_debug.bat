@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title DeskPet Debug
where py >nul 2>&1
if not errorlevel 1 goto USE_PY
where python >nul 2>&1
if not errorlevel 1 goto USE_PYTHON
echo [DeskPet] Python 3.10 or newer was not found.
pause
exit /b 1
:USE_PY
py -3 -X utf8 "%~dp0run_deskpet.py" --bootstrap --debug --diagnostics
goto FINISH
:USE_PYTHON
python -X utf8 "%~dp0run_deskpet.py" --bootstrap --debug --diagnostics
:FINISH
set "RESULT=%ERRORLEVEL%"
echo.
echo [DeskPet] Exit code: %RESULT%
echo Unified logs: %%LOCALAPPDATA%%\DeskPetClassic\logs\[session]\deskpet.log
pause
exit /b %RESULT%
