@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: 1. Try standard Python Launcher (py)
where py >nul 2>nul
if %errorlevel% equ 0 (
    py "%~dp0opener.py" "%~1"
    goto :check_error
)

:: 2. Try standard PATH python
where python >nul 2>nul
if %errorlevel% equ 0 (
    python "%~dp0opener.py" "%~1"
    goto :check_error
)

:: 3. Search User AppData Python installations (Python 3.x - 3.15+)
set "FOUND_PY="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%D\python.exe" set "FOUND_PY=%%D\python.exe"
)

if defined FOUND_PY (
    "%FOUND_PY%" "%~dp0opener.py" "%~1"
    goto :check_error
)

echo [ERROR] Python is not installed or could not be found.
echo Please run setup.bat first or install Python from python.org.
pause
exit /b 1

:check_error
if %errorlevel% neq 0 (
    echo.
    echo ========================================================
    echo   Something went wrong! Check python_log.txt in:
    echo   %~dp0
    echo ========================================================
    pause
)
