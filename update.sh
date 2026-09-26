#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "$0")"

echo "================================================================"
echo "Android Screenshot Tool - обновление"
echo "================================================================"

if [ -n "$(git status --porcelain)" ]; then
  echo "[ОШИБКА] В репозитории есть локальные изменения:"
  git status --short
  echo "Обновление остановлено, чтобы ничего не потерять."
  exit 1
fi

echo "[1/5] Получаю свежий main..."
git pull --ff-only origin main

echo "[2/5] Проверяю Python..."
command -v python3 >/dev/null 2>&1 || { echo "[ОШИБКА] python3 не найден."; exit 1; }
python3 -m py_compile android_screenshot_tool.py

echo "[3/5] Проверяю версию..."
python3 android_screenshot_tool.py --version

echo "[4/5] Проверяю ADB..."
if command -v adb >/dev/null 2>&1; then
  adb version | head -n 1
else
  echo "[ПРЕДУПРЕЖДЕНИЕ] adb не найден в PATH."
fi

echo "[5/5] Итоговое состояние Git..."
git status --short --branch
echo "Готово."
