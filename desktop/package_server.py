"""Build the embedded Python service used by the standalone Tauri package."""

import os
import shutil
import sys
from pathlib import Path

from PyInstaller.__main__ import run

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "src-tauri" / "resources" / "server"
WORK = ROOT / ".desktop-build" / "pyinstaller"
SEPARATOR = os.pathsep

DIST.mkdir(parents=True, exist_ok=True)
for name in ("airport-crm-server", "airport-crm-server.exe"):
    path = DIST / name
    if path.exists():
        path.unlink()

run([
    "--clean",
    "--noconfirm",
    "--onefile",
    "--name", "airport-crm-server",
    "--distpath", str(DIST),
    "--workpath", str(WORK),
    "--specpath", str(WORK),
    "--add-data", f"{ROOT / 'frontend'}{SEPARATOR}frontend",
    "--add-data", f"{ROOT / 'data'}{SEPARATOR}data",
    "--add-data", f"{ROOT / 'backend'}{SEPARATOR}backend",
    "--paths", str(ROOT),
    str(ROOT / "desktop" / "server_entry.py"),
])

expected = DIST / ("airport-crm-server.exe" if sys.platform == "win32" else "airport-crm-server")
if not expected.is_file():
    raise SystemExit(f"PyInstaller did not create {expected}")
print(f"Packaged service: {expected}")
