@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
where py >nul 2>&1
if not errorlevel 1 goto USE_PY
where python >nul 2>&1
if not errorlevel 1 goto USE_PYTHON
echo [DeskPet] Python 3.10 or newer was not found.
pause
exit /b 1
:USE_PY
py -3 -X utf8 "%~dp0run_deskpet.py" --bootstrap --windowed
goto FINISH
:USE_PYTHON
python -X utf8 "%~dp0run_deskpet.py" --bootstrap --windowed
:FINISH
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" exit /b 0
echo [DeskPet] Launch failed. Run start_deskpet_debug.bat for details.
pause
exit /b %RESULT%
