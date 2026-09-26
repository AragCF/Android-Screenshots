@echo off
setlocal EnableExtensions
set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
cd /d "%~dp0"

echo ================================================================
echo Android Screenshot Tool - build EXE
echo ================================================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3 and enable "Add Python to PATH".
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

set "VENV=.venv-build-win"
if not exist "%VENV%\Scripts\python.exe" (
    echo [1/5] Creating build environment...
    python -m venv "%VENV%"
    if errorlevel 1 goto :error
) else (
    echo [1/5] Build environment already exists.
)

set "PY=%VENV%\Scripts\python.exe"

echo [2/5] Updating pip...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/5] Installing build dependencies...
"%PY%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo [4/5] Building EXE...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --console --name AndroidScreenshotTool android_screenshot_tool.py
if errorlevel 1 goto :error

echo [5/5] Done.
echo.
echo EXE: %CD%\dist\AndroidScreenshotTool.exe
if "%NO_PAUSE%"=="0" pause
exit /b 0

:error
echo.
echo [ERROR] Build failed. Exit code: %ERRORLEVEL%
if "%NO_PAUSE%"=="0" pause
exit /b 1
