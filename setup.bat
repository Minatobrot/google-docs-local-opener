@echo off
chcp 65001 >nul
title Google Docs Opener - Setup
cd /d "%~dp0"

echo ========================================================
echo   Google Docs Local Opener - Dependency Installer
echo ========================================================
echo.

:: Locate Python automatically across standard Windows locations
set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py"
if not defined PYTHON_CMD (
    where python >nul 2>nul && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
        if exist "%%D\python.exe" set "PYTHON_CMD="%%D\python.exe""
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] Python was not found on your computer!
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Found Python: %PYTHON_CMD%
echo Installing required Google Drive API libraries...
echo.

%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install -r "%~dp0requirements.txt"

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to install libraries. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   Setup Complete!
echo   Next step: Place credentials.json in this folder
echo   and drag any .docx file onto run_docs.bat to test!
echo ========================================================
pause
