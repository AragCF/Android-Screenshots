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
set "VENV_REBUILD=0"
if exist "%VENV%\Scripts\python.exe" (
    "%VENV%\Scripts\python.exe" --version >nul 2>nul
    if errorlevel 1 set "VENV_REBUILD=1"
)

if "%VENV_REBUILD%"=="1" (
    echo [1/6] Existing build environment is stale. Recreating it...
    rmdir /S /Q "%VENV%"
    if exist "%VENV%" goto :error
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [1/6] Creating build environment...
    %PYTHON_CMD% -m venv "%VENV%"
    if errorlevel 1 goto :error
) else (
    echo [1/6] Build environment is healthy.
)

set "PY=%VENV%\Scripts\python.exe"

echo [2/6] Updating pip...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/6] Installing build dependencies...
"%PY%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo [4/6] Generating Windows version resources...
"%PY%" generate_windows_version_info.py
if errorlevel 1 goto :error
if not exist "windows_version_info.txt" goto :error

echo [5/6] Building EXE...
set "NEW_DIST=dist\_new_build"
set "NEW_WORK=build\_new_build"
if exist "%NEW_DIST%" rmdir /S /Q "%NEW_DIST%"
if exist "%NEW_WORK%" rmdir /S /Q "%NEW_WORK%"

"%PY%" -m PyInstaller --noconfirm --clean --onefile --console --name AndroidScreenshotTool --version-file "windows_version_info.txt" --distpath "%NEW_DIST%" --workpath "%NEW_WORK%" android_screenshot_tool.py
if errorlevel 1 goto :error

echo [6/6] Publishing EXE...
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
