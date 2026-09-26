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
echo Android Screenshot Tool - обновление
echo ================================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [ОШИБКА] Git не найден в PATH.
    goto :error
)

echo [1/6] Проверяю рабочее дерево...
for /f "delims=" %%L in ('git status --porcelain') do (
    echo [ОШИБКА] В репозитории есть локальные изменения:
    git status --short
    echo.
    echo Обновление остановлено, чтобы ничего не потерять.
    goto :error
)

echo [2/6] Получаю свежий main...
git pull --ff-only origin main
if errorlevel 1 goto :error

echo [3/6] Проверяю Python...
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

echo [4/6] Проверяю версию...
%PYTHON_CMD% android_screenshot_tool.py --version
if errorlevel 1 goto :error

echo [5/6] Проверяю ADB...
where adb >nul 2>nul
if errorlevel 1 (
    echo [ПРЕДУПРЕЖДЕНИЕ] adb не найден в PATH. Код обновлён, но для работы утилиты нужен ADB.
) else (
    adb version | findstr /B /C:"Android Debug Bridge" >nul 2>nul
    echo ADB найден.
)

echo [6/6] Итоговое состояние Git...
git status --short --branch

echo.
echo ================================================================
echo ОБНОВЛЕНИЕ ЗАВЕРШЕНО
echo Запуск: python android_screenshot_tool.py
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
