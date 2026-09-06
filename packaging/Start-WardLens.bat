@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "PYEXE=%~dp0runtime\python.exe"

if not exist "%PYEXE%" goto :MISSING_RUNTIME

echo WardLens is starting. Keep this window open while the app is running.
"%PYEXE%" -I -B -X utf8 -m wardlens %*
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" exit /b 0

echo.
echo WardLens stopped with exit code %RESULT%.
echo Run Diagnose-WardLens.bat and send WardLens-Diagnostics.txt.
echo The diagnostic file never contains clinical content or credentials.
echo.
if defined WARDLENS_NO_PAUSE exit /b %RESULT%
pause
exit /b %RESULT%

:MISSING_RUNTIME
echo.
echo [ERROR] The signed Python runtime is missing.
echo Extract the entire WardLens-Portable.zip before starting WardLens.
echo Do not copy Start-WardLens.bat by itself.
echo.
if defined WARDLENS_NO_PAUSE exit /b 2
pause
exit /b 2
