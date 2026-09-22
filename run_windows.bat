@echo off
setlocal

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3 and enable "Add Python to PATH".
    pause
    exit /b 1
)

python "%~dp0android_screenshot_tool.py"
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
pause
exit /b %RC%
