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
set "NEW_DIST=dist\_new_build"
set "NEW_WORK=build\_new_build"
if exist "%NEW_DIST%" rmdir /S /Q "%NEW_DIST%"
if exist "%NEW_WORK%" rmdir /S /Q "%NEW_WORK%"

"%PY%" -m PyInstaller --noconfirm --clean --onefile --console --name AndroidScreenshotTool --distpath "%NEW_DIST%" --workpath "%NEW_WORK%" android_screenshot_tool.py
if errorlevel 1 goto :error

echo [5/5] Publishing EXE...
if not exist "dist" mkdir "dist"

copy /Y "%NEW_DIST%\AndroidScreenshotTool.exe" "dist\AndroidScreenshotTool.exe" >nul 2>nul
if not errorlevel 1 (
    if exist "dist\AndroidScreenshotTool-next.exe" del /Q "dist\AndroidScreenshotTool-next.exe" >nul 2>nul
    echo.
    echo EXE: %CD%\dist\AndroidScreenshotTool.exe
    if "%NO_PAUSE%"=="0" pause
    exit /b 0
)

copy /Y "%NEW_DIST%\AndroidScreenshotTool.exe" "dist\AndroidScreenshotTool-next.exe" >nul
if errorlevel 1 goto :error

echo.
echo [WARNING] Current AndroidScreenshotTool.exe is running or locked.
echo New EXE was saved without terminating the running program:
echo %CD%\dist\AndroidScreenshotTool-next.exe
echo Close the old program before replacing the canonical EXE.
if "%NO_PAUSE%"=="0" pause
exit /b 0

:error
echo.
echo [ERROR] Build failed. Exit code: %ERRORLEVEL%
if "%NO_PAUSE%"=="0" pause
exit /b 1
