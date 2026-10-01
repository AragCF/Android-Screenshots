@echo off
setlocal EnableExtensions
set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
cd /d "%~dp0"

echo ================================================================
echo Android Screenshot Tool - build EXE
echo ================================================================

set "PYTHON_CMD="
python --version >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    if exist "C:\ProgramData\miniconda3\python.exe" (
        "C:\ProgramData\miniconda3\python.exe" --version >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=C:\ProgramData\miniconda3\python.exe"
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] Python 3 not found.
    echo Checked: python, py -3, C:\ProgramData\miniconda3\python.exe
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

echo Python: %PYTHON_CMD%

set "VENV=.venv-build-win"
if not exist "%VENV%\Scripts\python.exe" (
    echo [1/5] Creating build environment...
    %PYTHON_CMD% -m venv "%VENV%"
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
