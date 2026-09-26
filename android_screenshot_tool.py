#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import io
import json
import os
import re
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
APP_VERSION = "1.1.0"
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
    print("Для записи видео нужен FFmpeg.")
    print("Он не включён внутрь программы: устанавливаю штатным менеджером пакетов...")
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
        [path, "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if "libx264" not in result.stdout:
        raise RuntimeError(
            "Найденный FFmpeg не содержит кодировщик libx264. "
            "Для совместимого MP4 нужен FFmpeg с libx264."
        )
    return path


def detect_display_fps(device: AndroidDevice) -> float:
    candidates: list[float] = []

    try:
        result = run_adb(["-s", device.serial, "shell", "dumpsys", "display"], timeout=10)
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        patterns = [
            r"mRefreshRate\s*=\s*([0-9]+(?:\.[0-9]+)?)",
            r"refreshRate\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)",
            r"fps\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text, flags=re.I):
                try:
                    value = float(match)
                except ValueError:
                    continue
                if 20.0 <= value <= 240.0:
                    candidates.append(value)
    except RuntimeError:
        pass

    if candidates:
        return candidates[0]
    return 60.0


def build_video_paths(device: AndroidDevice) -> tuple[Path, Path, Path, Path]:
    output_dir = Path.cwd() / VIDEOS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    model = sanitize_filename(device.display_name)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stem = output_dir / f"{model}_{stamp}"

    final = stem.with_suffix(".mp4")
    recording = output_dir / f"{stem.name}.recording.mp4"
    mic = output_dir / f"{stem.name}.mic.wav"
    sidecar = output_dir / f"{stem.name}.recording.json"
    return final, recording, mic, sidecar


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
            path = result.stdout.strip().splitlines()
            if path:
                return path[0].strip()
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
                run_adb(["-s", device.serial, "shell", "rm", "-f", remote], timeout=5)
                record_args = tuple(x for x in args if x not in {"-t", "1"})
                if "-t" in args:
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
                    record_args = tuple(cleaned)
                return TinycapProfile(executable=executable, args=record_args)
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
    shell_command = (
        f"echo $$ > {remote_pid}; "
        f"exec {profile.executable} {remote_wav} {' '.join(profile.args)}"
    )
    return subprocess.Popen(
        [adb_path(), "-s", device.serial, "shell", "sh", "-c", shell_command],
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

    time.sleep(0.4)
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


def finalize_video(
    ffmpeg: str,
    recording: Path,
    final: Path,
    mic: Optional[Path] = None,
) -> bool:
    tmp = final.with_name(final.stem + ".finalizing.mp4")
    if tmp.exists():
        tmp.unlink()

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(recording),
    ]

    if mic and mic.exists() and mic.stat().st_size > 256:
        command += [
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
        ]
    else:
        command += ["-map", "0:v:0", "-c:v", "copy", "-an"]

    command += ["-movflags", "+faststart", str(tmp)]

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
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


def recover_interrupted_videos(ffmpeg: Optional[str]) -> list[str]:
    output_dir = Path.cwd() / VIDEOS_DIR
    if not output_dir.exists():
        return []

    messages: list[str] = []
    recordings = sorted(output_dir.glob("*.recording.mp4"))
    if not recordings:
        return messages

    if not ffmpeg:
        return [
            f"Найдены незавершённые видеозаписи: {len(recordings)}. "
            "Для автоматического восстановления нужен FFmpeg."
        ]

    for recording in recordings:
        base_name = recording.name.removesuffix(".recording.mp4")
        final = output_dir / f"{base_name}.mp4"
        mic = output_dir / f"{base_name}.mic.wav"
        if final.exists():
            final = output_dir / f"{base_name}_recovered.mp4"

        if finalize_video(ffmpeg, recording, final, mic if mic.exists() else None):
            messages.append(f"Восстановлена запись: {final}")
        else:
            messages.append(
                f"Не удалось автоматически восстановить: {recording}. "
                "Файл оставлен без изменений."
            )
    return messages


class VideoCaptureSession:
    def __init__(
        self,
        device: AndroidDevice,
        ffmpeg: str,
        target_fps: int,
        source_fps: float,
        recording_path: Path,
    ) -> None:
        self.device = device
        self.ffmpeg = ffmpeg
        self.target_fps = target_fps
        self.source_fps = source_fps
        self.recording_path = recording_path
        self.stop_event = threading.Event()
        self.error: Optional[str] = None
        self.bytes_received = 0
        self._adb_lock = threading.Lock()
        self._adb_process: Optional[subprocess.Popen] = None
        self._stderr_tail: list[str] = []
        self._thread: Optional[threading.Thread] = None
        self.remote_pid_path = "/data/local/tmp/android_screenshot_tool_screenrecord.pid"

        log_path = recording_path.with_suffix(".ffmpeg.log")
        self.log_path = log_path
        self._log_handle = log_path.open("wb")

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-r",
            f"{source_fps:.3f}",
            "-f",
            "h264",
            "-i",
            "pipe:0",
            "-vf",
            f"fps={target_fps}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "avc1",
            "-g",
            str(target_fps),
            "-keyint_min",
            str(target_fps),
            "-sc_threshold",
            "0",
            "-an",
            "-movflags",
            "+empty_moov+default_base_moof+frag_keyframe",
            "-frag_duration",
            "1000000",
            "-flush_packets",
            "1",
            "-f",
            "mp4",
            str(recording_path),
        ]

        self.ffmpeg_process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._log_handle,
        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._producer, daemon=True)
        self._thread.start()

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        if proc.stderr is None:
            return
        try:
            data = proc.stderr.read()
            if data:
                text = data.decode("utf-8", errors="replace")
                self._stderr_tail.append(text[-2000:])
                self._stderr_tail[:] = self._stderr_tail[-3:]
        except Exception:
            pass

    def _producer(self) -> None:
        restart_count = 0
        try:
            while not self.stop_event.is_set():
                remote_command = (
                    f"echo $ > {self.remote_pid_path}; "
                    "exec screenrecord --output-format=h264 -"
                )
                proc = subprocess.Popen(
                    [
                        adb_path(),
                        "-s",
                        self.device.serial,
                        "exec-out",
                        "sh",
                        "-c",
                        remote_command,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                with self._adb_lock:
                    self._adb_process = proc

                stderr_thread = threading.Thread(
                    target=self._read_stderr,
                    args=(proc,),
                    daemon=True,
                )
                stderr_thread.start()

                got_data = False
                assert proc.stdout is not None
                while not self.stop_event.is_set():
                    chunk = proc.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    got_data = True
                    self.bytes_received += len(chunk)
                    if self.ffmpeg_process.stdin is None:
                        raise RuntimeError("FFmpeg stdin недоступен")
                    try:
                        self.ffmpeg_process.stdin.write(chunk)
                        self.ffmpeg_process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        raise RuntimeError("FFmpeg неожиданно завершил приём видеопотока")

                if self.stop_event.is_set():
                    break

                rc = proc.wait(timeout=2)
                if not got_data or rc != 0:
                    detail = "\n".join(self._stderr_tail[-2:]).strip()
                    self.error = (
                        "Не удалось получить H.264-поток через Android screenrecord."
                        + (f"\n{detail}" if detail else "")
                    )
                    break

                restart_count += 1
                if restart_count > 1000:
                    self.error = "Слишком много автоматических перезапусков screenrecord."
                    break
                time.sleep(0.15)
        except Exception as exc:
            self.error = str(exc)
        finally:
            with self._adb_lock:
                self._adb_process = None
            if self.ffmpeg_process.stdin is not None:
                try:
                    self.ffmpeg_process.stdin.close()
                except OSError:
                    pass

    def is_running(self) -> bool:
        if self.error:
            return False
        if self.ffmpeg_process.poll() is not None:
            return False
        if self._thread and not self._thread.is_alive():
            return False
        return True

    def stop(self) -> None:
        self.stop_event.set()
        with self._adb_lock:
            proc = self._adb_process

        try:
            run_adb(
                [
                    "-s",
                    self.device.serial,
                    "shell",
                    "sh",
                    "-c",
                    (
                        f"if [ -f {self.remote_pid_path} ]; then "
                        f"kill -2 $(cat {self.remote_pid_path}) 2>/dev/null; fi"
                    ),
                ],
                timeout=4,
            )
        except RuntimeError:
            pass

        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                except OSError:
                    pass

        if self._thread:
            self._thread.join(timeout=8)

        try:
            run_adb(
                ["-s", self.device.serial, "shell", "rm", "-f", self.remote_pid_path],
                timeout=4,
            )
        except RuntimeError:
            pass

        if self.ffmpeg_process.stdin is not None and not self.ffmpeg_process.stdin.closed:
            try:
                self.ffmpeg_process.stdin.close()
            except OSError:
                pass

        try:
            self.ffmpeg_process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()

        try:
            self._log_handle.close()
        except OSError:
            pass

        if self.ffmpeg_process.returncode not in (0, None) and not self.error:
            self.error = f"FFmpeg завершился с кодом {self.ffmpeg_process.returncode}."

        if not self.error and self.log_path.exists():
            try:
                self.log_path.unlink()
            except OSError:
                pass


def record_video(
    device: AndroidDevice,
    target_fps: int,
    want_audio: bool,
    mic_profile_cache: dict[str, TinycapProfile],
) -> tuple[Optional[Path], Optional[str]]:
    ffmpeg = ensure_ffmpeg()
    source_fps = detect_display_fps(device)
    final, recording, mic, sidecar = build_video_paths(device)

    mic_profile: Optional[TinycapProfile] = None
    mic_process: Optional[subprocess.Popen] = None
    remote_wav = ""
    remote_pid = ""

    if want_audio:
        mic_profile = mic_profile_cache.get(device.serial)
        if mic_profile is None:
            clear_screen()
            print("Проверяю возможность захвата микрофона Android через tinycap...")
            mic_profile = probe_android_microphone(device)
            if mic_profile:
                mic_profile_cache[device.serial] = mic_profile

        if mic_profile:
            token = re.sub(r"[^A-Za-z0-9_]", "_", final.stem)
            remote_wav = f"/data/local/tmp/{token}.wav"
            remote_pid = f"/data/local/tmp/{token}.pid"
            sidecar.write_text(
                json.dumps(
                    {
                        "serial": device.serial,
                        "remote_wav": remote_wav,
                        "remote_pid": remote_pid,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            mic_process = start_tinycap(
                device,
                mic_profile,
                remote_wav,
                remote_pid,
            )

    session = VideoCaptureSession(
        device=device,
        ffmpeg=ffmpeg,
        target_fps=target_fps,
        source_fps=source_fps,
        recording_path=recording,
    )
    session.start()

    clear_screen()
    print(f"{APP_NAME} v{APP_VERSION}")
    print("=" * 72)
    print(f"Запись видео: {device.display_name} [{device.serial}]")
    print(f"Выходной FPS: {target_fps}")
    print(f"Определённая частота дисплея: {source_fps:.2f} Гц")
    if target_fps > source_fps + 0.5:
        print("Примечание: выходной FPS выше исходного — часть кадров будет повторяться.")
    print(
        "Микрофон Android: "
        + ("включён (tinycap)" if mic_profile else ("недоступен — запись без звука" if want_audio else "выключен"))
    )
    print()
    print("Файл во время записи защищён фрагментированным MP4.")
    print("Нажмите Enter или Esc для остановки.")
    print("=" * 72)

    while session.is_running():
        key = read_key_timeout(0.25)
        if key in {Key.ENTER, Key.ESC, "0"}:
            break

    session.stop()

    audio_ok = False
    if mic_profile and remote_wav and remote_pid:
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
        try:
            sidecar.unlink()
        except OSError:
            pass

    if session.error:
        return None, (
            session.error
            + "\nНезавершённый фрагментированный MP4 оставлен здесь: "
            + str(recording)
        )

    if not recording.exists() or recording.stat().st_size < 1024:
        return None, "Запись не создала пригодного видеопотока."

    if finalize_video(
        ffmpeg,
        recording,
        final,
        mic if audio_ok else None,
    ):
        note = None
        if want_audio and not audio_ok:
            note = (
                "Видео сохранено без звука: микрофон Android недоступен "
                "через tinycap на этом устройстве."
            )
        return final, note

    return recording, (
        "Не удалось выполнить финальную перепаковку. "
        "Оставлен фрагментированный MP4; он предназначен для аварийного восстановления."
    )


def choose_video_fps(current: int) -> int:
    items = [(str(index), f"{fps} FPS") for index, fps in enumerate(FPS_OPTIONS, start=1)]
    items.append(("0", "Назад"))
    selected_index = FPS_OPTIONS.index(current) if current in FPS_OPTIONS else FPS_OPTIONS.index(30)

    choice = menu_choice(
        "Частота кадров итогового MP4",
        items,
        selected_index=selected_index,
        footer=(
            "Если Android отдаёт меньше уникальных кадров, повышение FPS "
            "не создаёт новых деталей: недостающие кадры будут повторяться."
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
            ("1", f"FPS итогового MP4: {settings['video_fps']}"),
            ("2", f"Микрофон Android: {audio_label}"),
            ("0", "Назад"),
        ]
        choice = menu_choice(
            "Настройки видео",
            items,
            selected_index=selected_index,
            footer=(
                "FPS задаёт частоту итогового файла. "
                "Звук через стандартный screenrecord невозможен; "
                "микрофон используется только если устройство предоставляет tinycap."
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

            clear_screen()
            print("Проверяю микрофон Android. Это займёт около секунды...")
            profile = probe_android_microphone(selected_device)
            if profile is None:
                print()
                print("На этом устройстве микрофон через ADB недоступен.")
                print(
                    "Стандартный Android screenrecord звук не записывает. "
                    "Для универсального решения потребовалось бы отдельное Android-приложение "
                    "с разрешением RECORD_AUDIO."
                )
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
