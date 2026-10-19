#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).with_name("android_screenshot_tool.py")
OUTPUT = Path(__file__).with_name("windows_version_info.txt")

text = SOURCE.read_text(encoding="utf-8")
match = re.search(r'^APP_VERSION\s*=\s*"([0-9]+)\.([0-9]+)\.([0-9]+)"', text, re.M)
if not match:
    raise SystemExit("APP_VERSION not found")

major, minor, patch = (int(part) for part in match.groups())
version = f"{major}.{minor}.{patch}"
file_version = f"{version}.0"

resource = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'AragCF'),
          StringStruct('FileDescription', 'Android Screenshot Tool'),
          StringStruct('FileVersion', '{file_version}'),
          StringStruct('InternalName', 'AndroidScreenshotTool'),
          StringStruct('LegalCopyright', 'Copyright (c) 2026 AragCF'),
          StringStruct('OriginalFilename', 'AndroidScreenshotTool.exe'),
          StringStruct('ProductName', 'Android Screenshot Tool'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

OUTPUT.write_text(resource, encoding="utf-8", newline="\n")
print(f"Generated {OUTPUT.name} for {version}")
