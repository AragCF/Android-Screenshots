#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "$0")"

APP_NAME="android-screenshot-tool"
APP_VERSION="1.0.1"
VENV=".venv-build-linux"
DIST_DIR="dist"
WORK_DIR="build/deb"

echo "================================================================"
echo "Android Screenshot Tool - build DEB"
echo "================================================================"

have_sudo=0
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
  have_sudo=1
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
  have_sudo=1
else
  SUDO=""
fi

install_apt_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "[ОШИБКА] apt-get не найден. Установите вручную: python3 python3-venv python3-pip dpkg-dev"
    exit 1
  fi
  if [ "$have_sudo" -ne 1 ]; then
    echo "[ОШИБКА] Для автоматической установки пакетов нужны root-права или sudo."
    echo "Установите вручную: python3 python3-venv python3-pip dpkg-dev"
    exit 1
  fi
  $SUDO apt-get update
  $SUDO apt-get install -y python3 python3-venv python3-pip dpkg-dev
}

need_apt=0
command -v python3 >/dev/null 2>&1 || need_apt=1
command -v dpkg-deb >/dev/null 2>&1 || need_apt=1

if [ "$need_apt" -eq 1 ]; then
  echo "[1/7] Устанавливаю системные зависимости сборки..."
  install_apt_packages
else
  echo "[1/7] Системные зависимости найдены."
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "[2/7] Создаю изолированную среду сборки..."
  if ! python3 -m venv "$VENV"; then
    echo "Не удалось создать venv; пробую установить python3-venv."
    install_apt_packages
    python3 -m venv "$VENV"
  fi
else
  echo "[2/7] Среда сборки уже существует."
fi

PY="$VENV/bin/python"

echo "[3/7] Обновляю pip..."
"$PY" -m pip install --upgrade pip

echo "[4/7] Устанавливаю зависимости сборки..."
"$PY" -m pip install -r requirements-build.txt

echo "[5/7] Собираю автономный Linux-бинарник..."
"$PY" -m PyInstaller --noconfirm --clean --onefile --console --name "$APP_NAME" android_screenshot_tool.py

ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
if [ -z "$ARCH" ]; then
  case "$(uname -m)" in
    x86_64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    armv7l) ARCH="armhf" ;;
    *) ARCH="$(uname -m)" ;;
  esac
fi

PKG_ROOT="$WORK_DIR/${APP_NAME}_${APP_VERSION}_${ARCH}"
rm -rf "$PKG_ROOT"
mkdir -p "$PKG_ROOT/DEBIAN" "$PKG_ROOT/usr/bin" "$DIST_DIR"
install -m 0755 "$DIST_DIR/$APP_NAME" "$PKG_ROOT/usr/bin/$APP_NAME"

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: $APP_NAME
Version: $APP_VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: Local Build <local@localhost>
Description: Interactive ADB screenshot utility for USB and network Android devices
 Captures Android screenshots through adb, supports PNG and JPEG quality 99,
 device selection, arrow-key navigation, and timestamped filenames.
EOF

echo "[6/7] Собираю DEB-пакет..."
DEB_PATH="$DIST_DIR/${APP_NAME}_${APP_VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$PKG_ROOT" "$DEB_PATH"

echo "[7/7] Готово."
echo "DEB: $(pwd)/$DEB_PATH"
