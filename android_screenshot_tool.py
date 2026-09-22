#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

APP_NAME = "Android Screenshot Tool"
APP_VERSION = "1.0.0"
JPEG_QUALITY = 99
ADB_TIMEOUT = 30


@dataclass(frozen=True)
class AndroidDevice:
    serial: str
    state: str
    model: str

    @property
    def display_name(self) -> str:
        return self.model or self.serial

    @property
    def is_ready(self) -> bool:
        return self.state == "device"


class Key:
    UP = "UP"
    DOWN = "DOWN"
    ENTER = "ENTER"
    ESC = "ESC"


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def pause(message: str = "Нажмите любую клавишу для продолжения...") -> None:
    print()
    print(message)
    read_key()


def read_key() -> str:
    """Read one key without requiring Enter. Supports arrows on Windows and POSIX terminals."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {"H": Key.UP, "P": Key.DOWN}.get(code, "")
        if ch == "\r":
            return Key.ENTER
        if ch == "\x1b":
            return Key.ESC
        return ch

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch in ("\r", "\n"):
            return Key.ENTER
        if ch == "\x1b":
            # Arrow keys normally arrive as ESC [ A/B. Keep this short to avoid blocking.
            import select

            seq = ""
            for _ in range(2):
                ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                if not ready:
                    break
                seq += sys.stdin.read(1)
            if seq == "[A":
                return Key.UP
            if seq == "[B":
                return Key.DOWN
            return Key.ESC
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def sanitize_filename(value: str) -> str:
    value = value.strip().replace(" ", "_")
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1F]", "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    return value or "Android"


def adb_path() -> str:
    path = shutil.which("adb")
    if not path:
        raise RuntimeError("adb не найден в PATH. Установите Android Platform Tools и перезапустите консоль.")
    return path


def run_adb(args: Iterable[str], timeout: int = ADB_TIMEOUT, binary: bool = False):
    command = [adb_path(), *args]
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ADB не ответил за {timeout} секунд.") from exc
    except OSError as exc:
        raise RuntimeError(f"Не удалось запустить adb: {exc}") from exc


def parse_devices(output: str) -> list[AndroidDevice]:
    devices: list[AndroidDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("*"):
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        serial, state = parts[0], parts[1]
        model = ""
        for part in parts[2:]:
            if part.startswith("model:"):
                model = part.split(":", 1)[1].replace("_", " ")
                break

        devices.append(AndroidDevice(serial=serial, state=state, model=model))
    return devices


def get_device_model(serial: str) -> str:
    result = run_adb(["-s", serial, "shell", "getprop", "ro.product.model"])
    if result.returncode == 0:
        model = result.stdout.strip().replace("_", " ")
        if model:
            return model
    return serial


def list_devices() -> list[AndroidDevice]:
    result = run_adb(["devices", "-l"])
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip()
        raise RuntimeError(error or "Команда adb devices завершилась с ошибкой.")

    devices = parse_devices(result.stdout)
    enriched: list[AndroidDevice] = []
    for device in devices:
        model = device.model
        if device.is_ready and not model:
            try:
                model = get_device_model(device.serial)
            except RuntimeError:
                model = device.serial
        enriched.append(AndroidDevice(device.serial, device.state, model or device.serial))
    return enriched


def device_state_label(state: str) -> str:
    return {
        "device": "готово",
        "offline": "не в сети",
        "unauthorized": "нет разрешения",
        "authorizing": "ожидает разрешения",
    }.get(state, state)


def render_menu(title: str, lines: list[tuple[str, str]], selected_index: int, footer: Optional[str] = None) -> None:
    clear_screen()
    print(f"{APP_NAME} v{APP_VERSION}")
    print("=" * 64)
    if title:
        print(title)
        print("-" * 64)

    for index, (hotkey, label) in enumerate(lines):
        cursor = ">" if index == selected_index else " "
        print(f"{cursor} {hotkey}. {label}")

    print("-" * 64)
    print("Управление: ↑/↓, Enter или указанная цифра.")
    if footer:
        print(footer)


def menu_choice(title: str, items: list[tuple[str, str]], selected_index: int = 0, footer: Optional[str] = None) -> str:
    if not items:
        raise ValueError("Меню не может быть пустым")

    selected_index = max(0, min(selected_index, len(items) - 1))
    hotkeys = {hotkey: hotkey for hotkey, _ in items}

    while True:
        render_menu(title, items, selected_index, footer)
        key = read_key()
        if key == Key.UP:
            selected_index = (selected_index - 1) % len(items)
        elif key == Key.DOWN:
            selected_index = (selected_index + 1) % len(items)
        elif key == Key.ENTER:
            return items[selected_index][0]
        elif key in hotkeys:
            return key


def choose_device(current: Optional[AndroidDevice] = None) -> Optional[AndroidDevice]:
    while True:
        try:
            devices = list_devices()
        except RuntimeError as exc:
            clear_screen()
            print(f"Ошибка: {exc}")
            pause()
            return current

        if not devices:
            clear_screen()
            print("ADB-устройства не найдены.")
            print("Подключите устройство по USB или выполните adb connect <IP>:<PORT> для сетевого устройства.")
            print("После этого устройство появится в общем списке.")
            pause()
            return current

        menu: list[tuple[str, str]] = []
        index_map: dict[str, AndroidDevice] = {}
        for idx, device in enumerate(devices, start=1):
            key = str(idx)
            status = device_state_label(device.state)
            label = f"{device.display_name}  [{device.serial}]  — {status}"
            menu.append((key, label))
            index_map[key] = device

        menu.append(("0", "Назад"))
        choice = menu_choice("Выбор Android-устройства", menu)
        if choice == "0":
            return current

        device = index_map[choice]
        if not device.is_ready:
            clear_screen()
            print(f"Устройство {device.serial} сейчас недоступно: {device_state_label(device.state)}.")
            if device.state == "unauthorized":
                print("Разблокируйте Android и подтвердите RSA-разрешение на отладку по USB/ADB.")
            pause()
            continue
        return device


def ensure_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        pass

    if getattr(sys, "frozen", False):
        return False

    clear_screen()
    print("Для сохранения JPG нужна библиотека Pillow. Устанавливаю её автоматически...")
    commands = [
        [sys.executable, "-m", "pip", "install", "--user", "Pillow>=10.0"],
        [sys.executable, "-m", "pip", "install", "Pillow>=10.0"],
    ]

    for command in commands:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        if result.returncode == 0:
            try:
                import importlib

                importlib.invalidate_caches()
                from PIL import Image  # noqa: F401
                return True
            except ImportError:
                # A --user install may not become importable in the same interpreter on exotic setups.
                continue

    return False


def build_output_path(device: AndroidDevice, image_format: str) -> Path:
    model = sanitize_filename(device.display_name)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    extension = ".jpg" if image_format == "JPG" else ".png"
    base = Path.cwd() / f"{model}_{stamp}{extension}"
    if not base.exists():
        return base

    counter = 2
    while True:
        candidate = Path.cwd() / f"{model}_{stamp}_{counter}{extension}"
        if not candidate.exists():
            return candidate
        counter += 1


def capture_png(device: AndroidDevice) -> bytes:
    result = run_adb(["-s", device.serial, "exec-out", "screencap", "-p"], timeout=ADB_TIMEOUT, binary=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip() if result.stderr else ""
        raise RuntimeError(stderr or "Не удалось получить снимок экрана через adb screencap.")
    if not result.stdout or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("ADB вернул некорректные данные вместо PNG-снимка.")
    return result.stdout


def save_screenshot(device: AndroidDevice, image_format: str) -> Path:
    png_data = capture_png(device)
    output = build_output_path(device, image_format)

    if image_format == "PNG":
        output.write_bytes(png_data)
        return output

    if not ensure_pillow():
        raise RuntimeError(
            "Не удалось установить Pillow автоматически. Запустите: python -m pip install Pillow"
        )

    from PIL import Image

    with Image.open(io.BytesIO(png_data)) as image:
        image = image.convert("RGB")
        image.save(output, format="JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
    return output


def wait_for_adb_ready() -> None:
    result = run_adb(["start-server"], timeout=ADB_TIMEOUT)
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip()
        raise RuntimeError(error or "Не удалось запустить ADB server.")


def main() -> int:
    try:
        wait_for_adb_ready()
    except RuntimeError as exc:
        clear_screen()
        print(f"Ошибка: {exc}")
        return 2

    selected_device: Optional[AndroidDevice] = None
    image_format = "PNG"

    while True:
        selected_text = (
            f"{selected_device.display_name} [{selected_device.serial}]"
            if selected_device
            else "не выбрано"
        )
        items = [
            ("1", "Сменить устройство"),
            ("2", "Сделать снимок экрана"),
            ("3", f"Формат снимка: {image_format}" + (f" (качество {JPEG_QUALITY})" if image_format == "JPG" else "")),
            ("0", "Выйти"),
        ]
        choice = menu_choice(
            f"Выбранное устройство: {selected_text}",
            items,
            footer="USB и сетевые устройства ADB отображаются в одном списке.",
        )

        if choice == "0":
            clear_screen()
            return 0

        if choice == "1":
            selected_device = choose_device(selected_device)
            continue

        if choice == "3":
            if image_format == "PNG":
                if ensure_pillow():
                    image_format = "JPG"
                else:
                    clear_screen()
                    print("JPG недоступен: Pillow не удалось установить автоматически.")
                    pause()
            else:
                image_format = "PNG"
            continue

        if choice == "2":
            if selected_device is None:
                selected_device = choose_device(None)
                if selected_device is None:
                    continue

            # Refresh device state before each capture; this handles cable/network disconnects cleanly.
            try:
                current_devices = {d.serial: d for d in list_devices()}
            except RuntimeError as exc:
                clear_screen()
                print(f"Ошибка обновления списка устройств: {exc}")
                pause()
                continue

            refreshed = current_devices.get(selected_device.serial)
            if not refreshed or not refreshed.is_ready:
                clear_screen()
                print("Выбранное устройство больше не доступно. Выберите устройство заново.")
                pause()
                selected_device = choose_device(None)
                continue

            selected_device = refreshed
            clear_screen()
            print(f"Снимаю экран: {selected_device.display_name} [{selected_device.serial}]...")
            try:
                output = save_screenshot(selected_device, image_format)
            except RuntimeError as exc:
                print(f"Ошибка: {exc}")
                pause()
                continue

            print(f"Готово: {output}")
            pause()


if __name__ == "__main__":
    raise SystemExit(main())
