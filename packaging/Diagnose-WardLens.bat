@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONNOUSERSITE=1"
set "PYEXE=%~dp0runtime\python.exe"
set "LOG=%~dp0WardLens-Diagnostics.txt"

if not exist "%PYEXE%" goto :MISSING_RUNTIME

echo Running privacy-safe diagnostics...
"%PYEXE%" -I -B -X utf8 -m wardlens --diagnose > "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
type "%LOG%"
echo.
echo Diagnostic report: %LOG%
echo This report omits clinical content, credentials, and absolute user paths.
echo.
if defined WARDLENS_NO_PAUSE exit /b %RESULT%
pause
exit /b %RESULT%

:MISSING_RUNTIME
echo [ERROR] The signed Python runtime is missing. > "%LOG%"
type "%LOG%"
echo Extract the entire WardLens-Portable.zip before running diagnostics.
if defined WARDLENS_NO_PAUSE exit /b 2
pause
exit /b 2
