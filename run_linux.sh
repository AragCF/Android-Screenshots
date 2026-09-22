#!/usr/bin/env bash
set -u
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ОШИБКА] python3 не найден в PATH."
  exit 1
fi

python3 "$SCRIPT_DIR/android_screenshot_tool.py"
