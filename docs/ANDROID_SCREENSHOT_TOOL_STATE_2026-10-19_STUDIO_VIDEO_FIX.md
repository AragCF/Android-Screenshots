# Android Screenshot Tool — Studio video/toolchain fix state

**Date:** 2026-10-19  
**Project:** Android Screenshots  
**Canonical Studio path:** `E:\54\Projects\Android Screenshots\`  
**Current source version:** 1.1.8  
**Source commit:** `4eccf9385304fcf7ea4c9f25b8223acddb997ad6`

## Goal

Make Windows video recording independent of the interactive user's PATH, install WinGet/scrcpy machine-wide on Studio, embed proper Windows version metadata into the EXE, and verify real video recording on Vivo 1820 and Jetinno JL22.

## Confirmed root causes

1. Earlier EXE builds looked only at PATH / per-user WinGet locations and could miss scrcpy when launched from another Windows profile/session.
2. Studio's App Installer was installed, but the WinGet app execution alias was disabled, so plain `winget` was not reliably visible in all sessions.
3. scrcpy 5.x ships its own ADB; with several Android transports connected, that ADB could fail during scrcpy forwarding even though Android Screenshot Tool had already selected an exact serial.

## Implemented

- Machine-wide scrcpy:
  `C:\ProgramData\CreativeForceTeam\tools\scrcpy\5.0\scrcpy.exe`.
- Machine PATH contains:
  - `C:\ProgramData\CreativeForceTeam\bin`
  - `C:\ProgramData\CreativeForceTeam\tools\scrcpy\5.0`
- Machine wrappers for WinGet and scrcpy in `C:\ProgramData\CreativeForceTeam\bin`.
- Existing Studio profiles receive wrappers in their `WindowsApps` directory. Confirmed profiles:
  - `C:\Users\Andrey.DESKTOP-GT5M0VC`
  - `C:\Users\USER`
- Android Screenshot Tool 1.1.8 searches the machine scrcpy location directly, so video recording no longer depends on WinGet or user PATH at runtime.
- scrcpy is launched with environment variable `ADB` pointing to the exact ADB binary used by Android Screenshot Tool.
- EXE build now generates Windows version resources from `APP_VERSION`.

## Evidence

### Privileged machine-wide toolchain repair

GitHub Actions run: `39207379829`  
Runner: `aragcf-win-studio-privileged`  
Conclusion: **SUCCESS**

Confirmed:

- `APP_INSTALLER_VERSION=1.29.380.0`
- `CMD_WINGET=v1.29.380`
- machine scrcpy exists and supports `--record`
- wrappers created for both existing user profiles
- final marker: `ANDROID_SCREENSHOTS_MACHINE_TOOLCHAIN_PASS`

### Clean Studio postflight

GitHub Actions run: `39211633241`  
Runner: `aragcf-win-studio`  
Conclusion: **SUCCESS**

Confirmed from clean pinned source:

- source version: `Android Screenshot Tool 1.1.8`
- resolved scrcpy:
  `C:\ProgramData\CreativeForceTeam\tools\scrcpy\5.0\scrcpy.exe`
- Vivo 1820 video: **PASS**
- Jetinno JL22 video: **PASS**
- Windows EXE build: **PASS**
- EXE metadata:
  - FileVersion: `1.1.8.0`
  - ProductVersion: `1.1.8`
  - ProductName: `Android Screenshot Tool`
  - CompanyName: `AragCF`
  - FileDescription: `Android Screenshot Tool`
  - OriginalFilename: `AndroidScreenshotTool.exe`
  - LegalCopyright: `Copyright (c) 2026 AragCF`
- built SHA-256:
  `2fc14f8746aacc3f4138a8cbad86851c2148228efc6b2e0f6014d278982ef672`

## Remaining blocker

The currently running old `E:\54\Projects\Android Screenshots\dist\AndroidScreenshotTool.exe` locks its own file, therefore Windows refuses in-place replacement.

The verified 1.1.8 build is staged as:

`E:\54\Projects\Android Screenshots\dist\AndroidScreenshotTool-next.exe`

with the same verified metadata and SHA-256.

Do **not** force-kill the user's running application. After the user closes the old instance, replace the canonical EXE and verify version/hash.

## Progress

- completed: 4
- remaining: 1
- total: 5
- progress: 80%

## Next step

After the old AndroidScreenshotTool process is closed on Studio, copy the verified `AndroidScreenshotTool-next.exe` to `AndroidScreenshotTool.exe`, verify `--version`, FileVersion and SHA-256, then perform a short interactive video check if needed.

## What not to repeat

- Do not depend on per-user WinGet/scrcpy PATH for runtime.
- Do not use scrcpy's bundled ADB when the application already selected a device with another ADB client.
- Do not rebuild from an unpinned or dirty Studio worktree.
- Do not force-close the user's running EXE merely to replace the binary.

## Skills applied

- Creative Force Skills Router
- Developer Bugfix
- GitHub Runners Autonomous Work
- Windows Studio Build & Release
- TG Notify Project Notifications Standard
