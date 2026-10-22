@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Building Single-File GoogleDocsOpener.exe
cd /d "%~dp0"

if not exist "opener.py" (
    echo [ERROR] opener.py was not found in: "%~dp0"
    pause
    exit /b 1
)

:: Auto-detect Python launcher (py -3 or python)
set "PY_CMD="
py -3 --version >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
) else (
    python --version >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    echo [ERROR] Python is not installed or not in your system PATH!
    pause
    exit /b 1
)

echo ========================================================
echo   Using Python: %PY_CMD%
echo   Installing PyInstaller and Google API Libraries...
echo ========================================================
%PY_CMD% -m pip install --upgrade pip
%PY_CMD% -m pip install --upgrade --only-binary=:all: cryptography cffi
%PY_CMD% -m pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib rsa pyinstaller
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install required Python packages.
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   Stopping running instances and cleaning old builds...
echo ========================================================
taskkill /f /im GoogleDocsOpener.exe >nul 2>nul
timeout /t 1 /nobreak >nul
if exist "GoogleDocsOpener.exe" del /f /q "GoogleDocsOpener.exe"

echo.
echo ========================================================
echo   Compiling Standalone GoogleDocsOpener.exe...
echo ========================================================
%PY_CMD% opener.py --build
if errorlevel 1 (
    echo.
    echo [BUILD FAILED] PyInstaller encountered an error!
    pause
    exit /b 1
)

if not exist "GoogleDocsOpener.exe" (
    echo.
    echo [BUILD FAILED] GoogleDocsOpener.exe was not created!
    pause
    exit /b 1
)

echo.
echo ========================================================
echo   Running Post-Build Verification (--self-test)...
echo ========================================================
start "" /wait "%~dp0GoogleDocsOpener.exe" --self-test
if errorlevel 1 (
    echo [ERROR] GoogleDocsOpener.exe failed the post-build self-test!
    pause
    exit /b 1
)
echo [OK] Self-test passed! All DLLs and Google API modules loaded cleanly.

echo.
echo Cleaning up temporary PyInstaller build files...
if exist "build" rmdir /s /q "build" 2>nul
if exist "__pycache__" rmdir /s /q "__pycache__" 2>nul
if exist "GoogleDocsOpener.spec" del /f /q "GoogleDocsOpener.spec" 2>nul

echo.
echo ========================================================
echo   SUCCESS! GoogleDocsOpener.exe is built and verified!
echo ========================================================
pause