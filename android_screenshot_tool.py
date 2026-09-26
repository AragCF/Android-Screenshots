#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import io
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

APP_NAME = "Android Screenshot Tool"
APP_VERSION = "1.1.1"
JPEG_QUALITY = 99
ADB_TIMEOUT = 30
SCREENSHOTS_DIR = "Screenshots"
VIDEOS_DIR = "Videos"
FPS_OPTIONS = (6, 12, 24, 30, 45, 60)

DEFAULT_SETTINGS = {
    "last_device_serial": None,
    "image_format": "PNG",
    "video_fps": 30,
    "video_audio": False,
}


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


@dataclass(frozen=True)
class TinycapProfile:
    executable: str
    args: tuple[str, ...]


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


def read_key_timeout(timeout: float) -> Optional[str]:
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                return read_key()
            time.sleep(0.03)
        return None

    import select

    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if ready:
        return read_key()
    return None


def sanitize_filename(value: str) -> str:
    value = value.strip().replace(" ", "_")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    return value or "Android"


def config_dir() -> Path:
    if os.name == "nt":
        base = os.getenv("APPDATA")
        if base:
            return Path(base) / "AndroidScreenshotTool"
        return Path.home() / "AppData" / "Roaming" / "AndroidScreenshotTool"

    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "android-screenshot-tool"
    return Path.home() / ".config" / "android-screenshot-tool"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            settings.update(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    if settings.get("image_format") not in {"PNG", "JPG"}:
        settings["image_format"] = "PNG"
    if settings.get("video_fps") not in FPS_OPTIONS:
        settings["video_fps"] = 30
    settings["video_audio"] = bool(settings.get("video_audio", False))
    return settings


def save_settings(settings: dict) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def adb_path() -> str:
    path = shutil.which("adb")
    if not path:
        raise RuntimeError(
            "adb не найден в PATH. Установите Android Platform Tools и перезапустите консоль."
        )
    return path


def run_adb(
    args: Iterable[str],
    timeout: int = ADB_TIMEOUT,
    binary: bool = False,
) -> subprocess.CompletedProcess:
    command = [adb_path(), *args]
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
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


def render_menu(
    title: str,
    lines: list[tuple[str, str]],
    selected_index: int,
    footer: Optional[str] = None,
) -> None:
    clear_screen()
    print(f"{APP_NAME} v{APP_VERSION}")
    print("=" * 72)
    if title:
        print(title)
        print("-" * 72)

    for index, (hotkey, label) in enumerate(lines):
        cursor = ">" if index == selected_index else " "
        print(f"{cursor} {hotkey}. {label}")

    print("-" * 72)
    print("Управление: ↑/↓, Enter или указанная цифра.")
    if footer:
        print(footer)


def menu_choice(
    title: str,
    items: list[tuple[str, str]],
    selected_index: int = 0,
    footer: Optional[str] = None,
) -> str:
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
            print(
                "Подключите устройство по USB или выполните adb connect <IP>:<PORT> "
                "для сетевого устройства."
            )
            pause()
            return current

        menu: list[tuple[str, str]] = []
        index_map: dict[str, AndroidDevice] = {}
        selected_index = 0

        for idx, device in enumerate(devices, start=1):
            key = str(idx)
            status = device_state_label(device.state)
            label = f"{device.display_name}  [{device.serial}]  — {status}"
            menu.append((key, label))
            index_map[key] = device
            if current and device.serial == current.serial:
                selected_index = idx - 1

        menu.append(("0", "Назад"))
        choice = menu_choice(
            "Выбор Android-устройства",
            menu,
            selected_index=selected_index,
        )
        if choice == "0":
            return current

        device = index_map[choice]
        if not device.is_ready:
            clear_screen()
            print(
                f"Устройство {device.serial} сейчас недоступно: "
                f"{device_state_label(device.state)}."
            )
            if device.state == "unauthorized":
                print("Разблокируйте Android и подтвердите RSA-разрешение ADB.")
            pause()
            continue
        return device


def is_network_serial(serial: str) -> bool:
    return bool(re.match(r"^\[[^\]]+\]:\d+$", serial) or re.match(r"^[^:]+:\d+$", serial))


def auto_select_last_device(settings: dict) -> Optional[AndroidDevice]:
    serial = settings.get("last_device_serial")
    if not serial:
        return None

    if is_network_serial(serial):
        try:
            run_adb(["connect", serial], timeout=6)
        except RuntimeError:
            pass

    try:
        devices = list_devices()
    except RuntimeError:
        return None

    for device in devices:
        if device.serial == serial and device.is_ready:
            return device
    return None


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
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            try:
                import importlib

                importlib.invalidate_caches()
                from PIL import Image  # noqa: F401
                return True
            except ImportError:
                continue

    return False


def build_output_path(device: AndroidDevice, image_format: str) -> Path:
    model = sanitize_filename(device.display_name)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    extension = ".jpg" if image_format == "JPG" else ".png"
    output_dir = Path.cwd() / SCREENSHOTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"{model}_{stamp}{extension}"
    if not base.exists():
        return base

    counter = 2
    while True:
        candidate = output_dir / f"{model}_{stamp}_{counter}{extension}"
        if not candidate.exists():
            return candidate
        counter += 1


def capture_png(device: AndroidDevice) -> bytes:
    result = run_adb(
        ["-s", device.serial, "exec-out", "screencap", "-p"],
        timeout=ADB_TIMEOUT,
        binary=True,
    )
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
        image.save(
            output,
            format="JPEG",
            quality=JPEG_QUALITY,
            subsampling=0,
            optimize=True,
        )
    return output


def ffmpeg_candidates() -> list[Path]:
    result: list[Path] = []

    found = shutil.which("ffmpeg")
    if found:
        result.append(Path(found))

    if os.name == "nt":
        local = os.getenv("LOCALAPPDATA")
        if local:
            result.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")

    return result


def locate_ffmpeg() -> Optional[str]:
    for candidate in ffmpeg_candidates():
        if candidate.exists():
            return str(candidate)
    return None


def install_ffmpeg() -> Optional[str]:
    existing = locate_ffmpeg()
    if existing:
        return existing

    clear_screen()
    print("Для подготовки MP4 нужен FFmpeg.")
    print("Пробую установить его автоматически...")
    print()

    if os.name == "nt":
        winget = shutil.which("winget")
        if not winget:
            return None
        result = subprocess.run(
            [
                winget,
                "install",
                "--id",
                "Gyan.FFmpeg",
                "--exact",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            check=False,
        )
        if result.returncode != 0:
            return None
        return locate_ffmpeg()

    apt = shutil.which("apt-get")
    if apt:
        prefix: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            sudo = shutil.which("sudo")
            if not sudo:
                return None
            prefix = [sudo]
        subprocess.run([*prefix, apt, "update"], check=False)
        result = subprocess.run([*prefix, apt, "install", "-y", "ffmpeg"], check=False)
        if result.returncode == 0:
            return locate_ffmpeg()

    return None


def ensure_ffmpeg() -> str:
    path = locate_ffmpeg() or install_ffmpeg()
    if not path:
        raise RuntimeError(
            "FFmpeg не найден и автоматически установить его не удалось. "
            "Установите ffmpeg вручную и убедитесь, что команда ffmpeg доступна в PATH."
        )

    result = subprocess.run(
        [path, "-hide_banner", "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("FFmpeg найден, но не запускается.")
    return path


def scrcpy_candidates() -> list[Path]:
    result: list[Path] = []

    found = shutil.which("scrcpy")
    if found:
        result.append(Path(found))

    if os.name == "nt":
        local = os.getenv("LOCALAPPDATA")
        if local:
            result.append(Path(local) / "Microsoft" / "WinGet" / "Links" / "scrcpy.exe")

    return result


def locate_scrcpy() -> Optional[str]:
    for candidate in scrcpy_candidates():
        if candidate.exists():
            return str(candidate)
    return None


def install_scrcpy() -> Optional[str]:
    existing = locate_scrcpy()
    if existing:
        return existing

    clear_screen()
    print("Для надёжной записи видео нужен scrcpy.")
    print("Пробую установить его автоматически...")
    print()

    if os.name == "nt":
        winget = shutil.which("winget")
        if not winget:
            return None
        result = subprocess.run(
            [
                winget,
                "install",
                "--id",
                "Genymobile.scrcpy",
                "--exact",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            check=False,
        )
        if result.returncode != 0:
            return None
        return locate_scrcpy()

    apt = shutil.which("apt-get")
    if apt:
        prefix: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            sudo = shutil.which("sudo")
            if not sudo:
                return None
            prefix = [sudo]
        subprocess.run([*prefix, apt, "update"], check=False)
        result = subprocess.run([*prefix, apt, "install", "-y", "scrcpy"], check=False)
        if result.returncode == 0:
            return locate_scrcpy()

    return None


def ensure_scrcpy() -> tuple[str, str]:
    path = locate_scrcpy() or install_scrcpy()
    if not path:
        raise RuntimeError(
            "scrcpy не найден и автоматически установить его не удалось. "
            "На Windows выполните: winget install --exact Genymobile.scrcpy"
        )

    result = subprocess.run(
        [path, "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0 or "--record" not in result.stdout:
        raise RuntimeError("scrcpy найден, но его версия не поддерживает запись.")
    return path, result.stdout


def get_android_api(device: AndroidDevice) -> int:
    try:
        result = run_adb(
            ["-s", device.serial, "shell", "getprop", "ro.build.version.sdk"],
            timeout=5,
        )
        return int(result.stdout.strip())
    except (RuntimeError, ValueError, AttributeError):
        return 0


def build_video_paths(device: AndroidDevice) -> tuple[Path, Path, Path, Path]:
    output_dir = Path.cwd() / VIDEOS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    model = sanitize_filename(device.display_name)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stem = output_dir / f"{model}_{stamp}"

    final = stem.with_suffix(".mp4")
    recording = output_dir / f"{stem.name}.recording.mkv"
    mic = output_dir / f"{stem.name}.mic.wav"
    log = output_dir / f"{stem.name}.scrcpy.log"
    return final, recording, mic, log


def find_tinycap(device: AndroidDevice) -> Optional[str]:
    commands = [
        "command -v tinycap 2>/dev/null",
        "which tinycap 2>/dev/null",
        "ls /system/bin/tinycap 2>/dev/null",
        "ls /vendor/bin/tinycap 2>/dev/null",
    ]
    for command in commands:
        try:
            result = run_adb(
                ["-s", device.serial, "shell", "sh", "-c", command],
                timeout=5,
            )
        except RuntimeError:
            continue
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if lines:
                return lines[0].strip()
    return None


def probe_android_microphone(device: AndroidDevice) -> Optional[TinycapProfile]:
    executable = find_tinycap(device)
    if not executable:
        return None

    profiles = [
        ("-r", "48000", "-b", "16", "-c", "1", "-t", "1"),
        ("-r", "48000", "-b", "16", "-c", "2", "-t", "1"),
        ("-t", "1"),
    ]
    remote = "/data/local/tmp/android_screenshot_tool_mic_probe.wav"

    for args in profiles:
        try:
            run_adb(["-s", device.serial, "shell", "rm", "-f", remote], timeout=5)
            result = run_adb(
                ["-s", device.serial, "shell", executable, remote, *args],
                timeout=8,
            )
            if result.returncode != 0:
                continue

            size_result = run_adb(
                ["-s", device.serial, "shell", "sh", "-c", f"wc -c < {remote}"],
                timeout=5,
            )
            try:
                size = int(size_result.stdout.strip())
            except (ValueError, AttributeError):
                size = 0

            if size > 256:
                cleaned: list[str] = []
                skip = False
                for item in args:
                    if skip:
                        skip = False
                        continue
                    if item == "-t":
                        skip = True
                        continue
                    cleaned.append(item)
                return TinycapProfile(executable=executable, args=tuple(cleaned))
        except RuntimeError:
            continue
        finally:
            try:
                run_adb(["-s", device.serial, "shell", "rm", "-f", remote], timeout=5)
            except RuntimeError:
                pass

    return None


def start_tinycap(
    device: AndroidDevice,
    profile: TinycapProfile,
    remote_wav: str,
    remote_pid: str,
) -> subprocess.Popen:
    command = (
        f"echo $$ > {remote_pid}; "
        f"exec {profile.executable} {remote_wav} {' '.join(profile.args)}"
    )
    return subprocess.Popen(
        [adb_path(), "-s", device.serial, "shell", "sh", "-c", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def stop_tinycap(
    device: AndroidDevice,
    process: Optional[subprocess.Popen],
    remote_wav: str,
    remote_pid: str,
    local_wav: Path,
) -> bool:
    try:
        run_adb(
            [
                "-s",
                device.serial,
                "shell",
                "sh",
                "-c",
                f"if [ -f {remote_pid} ]; then kill -2 $(cat {remote_pid}) 2>/dev/null; fi",
            ],
            timeout=5,
        )
    except RuntimeError:
        pass

    if process is not None:
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    time.sleep(0.3)
    pull = run_adb(["-s", device.serial, "pull", remote_wav, str(local_wav)], timeout=30)
    ok = pull.returncode == 0 and local_wav.exists() and local_wav.stat().st_size > 256

    try:
        run_adb(
            ["-s", device.serial, "shell", "rm", "-f", remote_wav, remote_pid],
            timeout=5,
        )
    except RuntimeError:
        pass
    return ok


def _run_ffmpeg_finalize(command: list[str], tmp: Path) -> bool:
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024


def finalize_video(
    ffmpeg: str,
    recording: Path,
    final: Path,
    mic: Optional[Path] = None,
    mic_delay: float = 0.0,
) -> bool:
    tmp = final.with_name(final.stem + ".finalizing.mp4")
    if tmp.exists():
        tmp.unlink()

    base = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(recording),
    ]

    if mic and mic.exists() and mic.stat().st_size > 256:
        command = base + [
            "-itsoffset",
            f"{max(0.0, mic_delay):.3f}",
            "-i",
            str(mic),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        ok = _run_ffmpeg_finalize(command, tmp)
    else:
        command = base + [
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        ok = _run_ffmpeg_finalize(command, tmp)

        if not ok:
            if tmp.exists():
                tmp.unlink()
            command = base + [
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(tmp),
            ]
            ok = _run_ffmpeg_finalize(command, tmp)

    if not ok:
        if tmp.exists():
            tmp.unlink()
        return False

    os.replace(tmp, final)
    try:
        recording.unlink()
    except OSError:
        pass
    if mic:
        try:
            mic.unlink()
        except OSError:
            pass
    return True


def _unique_recovered_path(output_dir: Path, base_name: str) -> Path:
    first = output_dir / f"{base_name}_recovered.mp4"
    if not first.exists():
        return first
    counter = 2
    while True:
        candidate = output_dir / f"{base_name}_recovered_{counter}.mp4"
        if not candidate.exists():
            return candidate
        counter += 1


def quarantine_legacy_recordings() -> list[str]:
    output_dir = Path.cwd() / VIDEOS_DIR
    if not output_dir.exists():
        return []

    recordings = sorted(output_dir.glob("*.recording.mp4"))
    if not recordings:
        return []

    legacy_dir = output_dir / "Legacy_1.1.0"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    moved = 0

    for recording in recordings:
        base = recording.name.removesuffix(".recording.mp4")
        related = [
            recording,
            output_dir / f"{base}.recording.ffmpeg.log",
            output_dir / f"{base}.mic.wav",
            output_dir / f"{base}.recording.json",
        ]
        for path in related:
            if not path.exists():
                continue
            target = legacy_dir / path.name
            if target.exists():
                target = legacy_dir / f"{int(time.time())}_{path.name}"
            try:
                shutil.move(str(path), str(target))
            except OSError:
                continue
        moved += 1

    if moved:
        return [
            f"Найдено старых незавершённых записей видеодвижка 1.1.0: {moved}. "
            f"Они перенесены без повторной обработки в {legacy_dir}"
        ]
    return []


def _file_is_stable(path: Path) -> bool:
    try:
        size1 = path.stat().st_size
        time.sleep(0.5)
        size2 = path.stat().st_size
        return size1 == size2
    except OSError:
        return False


def recover_interrupted_videos(ffmpeg: Optional[str]) -> list[str]:
    messages = quarantine_legacy_recordings()

    output_dir = Path.cwd() / VIDEOS_DIR
    if not output_dir.exists():
        return messages

    recordings = sorted(output_dir.glob("*.recording.mkv"))
    if not recordings:
        return messages

    if not ffmpeg:
        messages.append(
            f"Найдены незавершённые новые видеозаписи: {len(recordings)}. "
            "Для автоматического восстановления нужен FFmpeg."
        )
        return messages

    for recording in recordings:
        if not _file_is_stable(recording):
            messages.append(f"Запись ещё изменяется и пока не восстанавливается: {recording}")
            continue

        base_name = recording.name.removesuffix(".recording.mkv")
        final = output_dir / f"{base_name}.mp4"
        if final.exists():
            final = _unique_recovered_path(output_dir, base_name)

        if finalize_video(ffmpeg, recording, final):
            messages.append(f"Восстановлена запись: {final}")
        else:
            messages.append(
                f"Не удалось автоматически восстановить: {recording}. "
                "Исходный MKV оставлен без изменений."
            )

    return messages


def _scrcpy_record_command(
    scrcpy: str,
    help_text: str,
    device: AndroidDevice,
    recording: Path,
    max_fps: int,
    use_scrcpy_mic: bool,
) -> list[str]:
    command = [scrcpy, "--serial", device.serial, "--record", str(recording)]

    if "--record-format" in help_text:
        command.append("--record-format=mkv")

    if "--no-playback" in help_text:
        command.append("--no-playback")
    elif "--no-display" in help_text:
        command.append("--no-display")

    if "--no-window" in help_text:
        command.append("--no-window")
    if "--no-control" in help_text:
        command.append("--no-control")

    if "--max-fps" in help_text:
        command.append(f"--max-fps={max_fps}")

    if "--capture-orientation" in help_text:
        command.append("--capture-orientation=@")
    elif "--lock-video-orientation" in help_text:
        command.append("--lock-video-orientation")

    if "--video-codec" in help_text:
        command.append("--video-codec=h264")

    if use_scrcpy_mic:
        command.append("--audio-source=mic")
        if "--audio-codec" in help_text:
            command.append("--audio-codec=aac")
        if "--require-audio" in help_text:
            command.append("--require-audio")
    elif "--no-audio" in help_text:
        command.append("--no-audio")

    return command


def _stop_scrcpy(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return

    try:
        if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass

    try:
        process.kill()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _tail_text(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
        return data[-limit:].strip()
    except OSError:
        return ""


def record_video(
    device: AndroidDevice,
    target_fps: int,
    want_audio: bool,
    mic_profile_cache: dict[str, TinycapProfile],
) -> tuple[Optional[Path], Optional[str]]:
    ffmpeg = ensure_ffmpeg()
    scrcpy, help_text = ensure_scrcpy()
    final, recording, mic, log = build_video_paths(device)

    api = get_android_api(device)
    use_scrcpy_mic = bool(
        want_audio
        and api >= 30
        and "--audio-source" in help_text
    )

    tinycap_profile: Optional[TinycapProfile] = None
    mic_process: Optional[subprocess.Popen] = None
    remote_wav = ""
    remote_pid = ""
    mic_delay = 0.0
    audio_note: Optional[str] = None

    if want_audio and not use_scrcpy_mic:
        tinycap_profile = mic_profile_cache.get(device.serial)
        if tinycap_profile is None:
            tinycap_profile = probe_android_microphone(device)
            if tinycap_profile:
                mic_profile_cache[device.serial] = tinycap_profile

        if tinycap_profile is None:
            audio_note = (
                "Микрофон недоступен: на Android ниже 11 scrcpy не умеет захватывать "
                "аудио, а tinycap на этой прошивке недоступен. Видео записано без звука."
            )

    command = _scrcpy_record_command(
        scrcpy,
        help_text,
        device,
        recording,
        target_fps,
        use_scrcpy_mic,
    )

    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    log_handle = log.open("wb")
    video_start = time.monotonic()
    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    if tinycap_profile:
        token = re.sub(r"[^A-Za-z0-9_]", "_", final.stem)
        remote_wav = f"/data/local/tmp/{token}.wav"
        remote_pid = f"/data/local/tmp/{token}.pid"
        mic_start = time.monotonic()
        mic_delay = max(0.0, mic_start - video_start)
        mic_process = start_tinycap(
            device,
            tinycap_profile,
            remote_wav,
            remote_pid,
        )

    time.sleep(0.8)
    if process.poll() is not None:
        log_handle.close()
        detail = _tail_text(log)
        return None, (
            "scrcpy не смог начать запись."
            + (f"\n{detail}" if detail else "")
        )

    clear_screen()
    print(f"{APP_NAME} v{APP_VERSION}")
    print("=" * 72)
    print(f"Запись видео: {device.display_name} [{device.serial}]")
    print(f"Лимит FPS: {target_fps}")
    print("Ориентация: фиксируется по текущей ориентации устройства при старте записи.")
    if use_scrcpy_mic:
        print("Микрофон Android: включён через scrcpy.")
    elif tinycap_profile:
        print("Микрофон Android: включён через tinycap.")
    else:
        print("Микрофон Android: выключен." if not want_audio else "Микрофон Android: недоступен.")
    print()
    print("Во время записи используется временный MKV; после остановки он быстро")
    print("перепаковывается без перекодирования в MP4 с faststart.")
    print("Нажмите Enter или Esc для остановки.")
    print("=" * 72)

    while process.poll() is None:
        key = read_key_timeout(0.25)
        if key in {Key.ENTER, Key.ESC, "0"}:
            break

    _stop_scrcpy(process)
    log_handle.close()

    audio_ok = False
    if tinycap_profile and remote_wav and remote_pid:
        try:
            audio_ok = stop_tinycap(
                device,
                mic_process,
                remote_wav,
                remote_pid,
                mic,
            )
        except Exception:
            audio_ok = False

    if not recording.exists() or recording.stat().st_size < 1024:
        detail = _tail_text(log)
        return None, (
            "scrcpy не создал пригодный видеофайл."
            + (f"\n{detail}" if detail else "")
        )

    if finalize_video(
        ffmpeg,
        recording,
        final,
        mic if audio_ok else None,
        mic_delay=mic_delay,
    ):
        try:
            log.unlink()
        except OSError:
            pass

        if want_audio and tinycap_profile and not audio_ok:
            audio_note = "Видео сохранено, но tinycap не вернул пригодную аудиодорожку."
        return final, audio_note

    return recording, (
        "Не удалось перепаковать MKV в MP4. Временный MKV оставлен без изменений; "
        "его можно открыть непосредственно или восстановить при следующем запуске."
    )


def choose_video_fps(current: int) -> int:
    items = [(str(index), f"до {fps} FPS") for index, fps in enumerate(FPS_OPTIONS, start=1)]
    items.append(("0", "Назад"))
    selected_index = FPS_OPTIONS.index(current) if current in FPS_OPTIONS else FPS_OPTIONS.index(30)

    choice = menu_choice(
        "Ограничение частоты кадров",
        items,
        selected_index=selected_index,
        footer=(
            "scrcpy ограничивает максимальную частоту захвата. Реальная частота может быть "
            "ниже, если содержимое экрана обновляется реже."
        ),
    )
    if choice == "0":
        return current
    return FPS_OPTIONS[int(choice) - 1]


def video_settings_menu(
    settings: dict,
    selected_device: Optional[AndroidDevice],
    mic_profile_cache: dict[str, TinycapProfile],
) -> Optional[AndroidDevice]:
    selected_index = 0

    while True:
        audio_label = "включён" if settings["video_audio"] else "выключен"
        items = [
            ("1", f"Лимит FPS: {settings['video_fps']}"),
            ("2", f"Микрофон Android: {audio_label}"),
            ("0", "Назад"),
        ]
        choice = menu_choice(
            "Настройки видео",
            items,
            selected_index=selected_index,
            footer=(
                "Видео записывает scrcpy. Микрофон штатно доступен через scrcpy на Android 11+, "
                "а на старых прошивках программа дополнительно пробует tinycap."
            ),
        )

        if choice == "0":
            return selected_device

        if choice == "1":
            selected_index = 0
            settings["video_fps"] = choose_video_fps(settings["video_fps"])
            save_settings(settings)
            continue

        if choice == "2":
            selected_index = 1
            if settings["video_audio"]:
                settings["video_audio"] = False
                save_settings(settings)
                continue

            if selected_device is None:
                selected_device = choose_device(None)
                if selected_device is None:
                    continue

            api = get_android_api(selected_device)
            if api >= 30:
                settings["video_audio"] = True
                save_settings(settings)
                continue

            clear_screen()
            print("Android ниже 11: проверяю дополнительный путь через tinycap...")
            profile = probe_android_microphone(selected_device)
            if profile is None:
                print()
                print("Микрофон на этой прошивке через ADB недоступен.")
                print("Видео по-прежнему можно записывать без звука.")
                settings["video_audio"] = False
                save_settings(settings)
                pause()
                continue

            mic_profile_cache[selected_device.serial] = profile
            settings["video_audio"] = True
            save_settings(settings)


def wait_for_adb_ready() -> None:
    result = run_adb(["start-server"], timeout=ADB_TIMEOUT)
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip()
        raise RuntimeError(error or "Не удалось запустить ADB server.")


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] in {"--version", "-V"}:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0

    settings = load_settings()

    try:
        wait_for_adb_ready()
    except RuntimeError as exc:
        clear_screen()
        print(f"Ошибка: {exc}")
        return 2

    selected_device = auto_select_last_device(settings)
    image_format = settings["image_format"]
    main_selected_index = 0
    mic_profile_cache: dict[str, TinycapProfile] = {}

    ffmpeg = locate_ffmpeg()
    recovery_messages = recover_interrupted_videos(ffmpeg)
    if recovery_messages:
        clear_screen()
        print("Восстановление предыдущих видеозаписей:")
        print()
        for message in recovery_messages:
            print(message)
        pause()

    while True:
        selected_text = (
            f"{selected_device.display_name} [{selected_device.serial}]"
            if selected_device
            else "не выбрано"
        )
        audio_label = "звук" if settings["video_audio"] else "без звука"
        items = [
            ("1", "Сменить устройство"),
            ("2", "Сделать снимок экрана"),
            ("3", "Записать видео"),
            ("4", f"Формат снимка: {image_format}" + (f" (качество {JPEG_QUALITY})" if image_format == "JPG" else "")),
            ("5", f"Настройки видео: {settings['video_fps']} FPS, {audio_label}"),
            ("0", "Выйти"),
        ]
        choice = menu_choice(
            f"Выбранное устройство: {selected_text}",
            items,
            selected_index=main_selected_index,
            footer="USB и сетевые устройства ADB отображаются в одном списке.",
        )

        if choice == "0":
            clear_screen()
            return 0

        if choice == "1":
            main_selected_index = 0
            selected_device = choose_device(selected_device)
            if selected_device:
                settings["last_device_serial"] = selected_device.serial
                save_settings(settings)
            continue

        if choice == "4":
            main_selected_index = 3
            if image_format == "PNG":
                if ensure_pillow():
                    image_format = "JPG"
                else:
                    clear_screen()
                    print("JPG недоступен: Pillow не удалось установить автоматически.")
                    pause()
            else:
                image_format = "PNG"
            settings["image_format"] = image_format
            save_settings(settings)
            continue

        if choice == "5":
            main_selected_index = 4
            selected_device = video_settings_menu(
                settings,
                selected_device,
                mic_profile_cache,
            )
            if selected_device:
                settings["last_device_serial"] = selected_device.serial
                save_settings(settings)
            continue

        if choice in {"2", "3"}:
            main_selected_index = 1 if choice == "2" else 2
            if selected_device is None:
                selected_device = choose_device(None)
                if selected_device is None:
                    continue
                settings["last_device_serial"] = selected_device.serial
                save_settings(settings)

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
                if selected_device:
                    settings["last_device_serial"] = selected_device.serial
                    save_settings(settings)
                continue

            selected_device = refreshed

        if choice == "2":
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
            continue

        if choice == "3":
            try:
                output, note = record_video(
                    selected_device,
                    settings["video_fps"],
                    settings["video_audio"],
                    mic_profile_cache,
                )
            except RuntimeError as exc:
                clear_screen()
                print(f"Ошибка записи видео: {exc}")
                pause()
                continue

            clear_screen()
            if output:
                print(f"Видео сохранено: {output}")
            if note:
                print()
                print(note)
            pause()


if __name__ == "__main__":
    raise SystemExit(main())
