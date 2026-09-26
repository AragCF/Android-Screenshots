@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>nul

if /I "%~1"=="--worker" goto :worker

set "TMP_UPDATE=%TEMP%\AndroidScreenshotTool_update_%RANDOM%_%RANDOM%.bat"
copy /Y "%~f0" "%TMP_UPDATE%" >nul
if errorlevel 1 (
    echo [ОШИБКА] Не удалось создать временную копию update.bat.
    pause
    exit /b 1
)

call "%TMP_UPDATE%" --worker "%~dp0"
set "RC=%ERRORLEVEL%"
del /Q "%TMP_UPDATE%" >nul 2>nul
exit /b %RC%

:worker
set "REPO=%~2"
cd /d "%REPO%"
if errorlevel 1 goto :error

echo ================================================================
echo Android Screenshot Tool - полное обновление
echo ================================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Git не найден в PATH.
    goto :error
)

echo [1/8] Проверяю рабочее дерево...
for /f "delims=" %%L in ('git status --porcelain') do (
    echo [ОШИБКА] В репозитории есть локальные изменения:
    git status --short
    echo.
    echo Обновление остановлено, чтобы ничего не потерять.
    goto :error
)

echo [2/8] Получаю свежий main...
git pull --ff-only origin main
if errorlevel 1 goto :error

echo [3/8] Проверяю Python...
set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    where py >nul 2>nul && set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    echo [ОШИБКА] Python 3 не найден.
    goto :error
)

%PYTHON_CMD% -m py_compile android_screenshot_tool.py
if errorlevel 1 goto :error
%PYTHON_CMD% android_screenshot_tool.py --version
if errorlevel 1 goto :error

echo [4/8] Проверяю ADB...
where adb >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] adb не найден в PATH.
    goto :error
)
adb version | findstr /B /C:"Android Debug Bridge" >nul 2>nul
if errorlevel 1 (
    echo [ПРЕДУПРЕЖДЕНИЕ] adb найден, но проверка версии дала неожиданный ответ.
) else (
    echo ADB найден.
)

echo [5/8] Проверяю scrcpy...
where scrcpy >nul 2>nul
if errorlevel 1 (
    where winget >nul 2>nul
    if not errorlevel 1 (
        echo Устанавливаю scrcpy через WinGet...
        winget install --id Genymobile.scrcpy --exact --source winget --accept-package-agreements --accept-source-agreements
    ) else (
        echo [ПРЕДУПРЕЖДЕНИЕ] scrcpy не найден, WinGet недоступен.
        echo Программа попробует установить scrcpy при первой записи видео.
    )
) else (
    echo scrcpy найден.
)

echo [6/8] Проверяю FFmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
    where winget >nul 2>nul
    if not errorlevel 1 (
        echo Устанавливаю FFmpeg через WinGet...
        winget install --id Gyan.FFmpeg --exact --source winget --accept-package-agreements --accept-source-agreements
    ) else (
        echo [ПРЕДУПРЕЖДЕНИЕ] FFmpeg не найден, WinGet недоступен.
        echo Программа попробует установить FFmpeg при первой записи видео.
    )
) else (
    echo FFmpeg найден.
)

echo [7/8] Пересобираю Windows EXE...
call build_exe.bat --no-pause
if errorlevel 1 goto :error

echo [8/8] Итоговое состояние Git...
git status --short --branch

echo.
echo ================================================================
echo ОБНОВЛЕНИЕ ЗАВЕРШЕНО
echo Версия:
%PYTHON_CMD% android_screenshot_tool.py --version
echo.
echo EXE:
echo   %CD%\dist\AndroidScreenshotTool.exe
echo ================================================================
pause
exit /b 0

:error
echo.
echo ================================================================
echo ОБНОВЛЕНИЕ НЕ ЗАВЕРШЕНО
echo Репозиторий не подвергался принудительному сбросу или очистке.
echo ================================================================
pause
exit /b 1
