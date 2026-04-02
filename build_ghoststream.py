#!/usr/bin/env python3
"""
GhostStream - Local Build Script
Build standalone executables for your current platform.

Usage:
    python build_ghoststream.py

Requirements:
    pip install pyinstaller
"""

import os
import sys
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRYPOINT = ROOT / "ghoststream" / "launcher.py"
DEFAULT_CONFIG = ROOT / "ghoststream.yaml"


def get_platform():
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    return "linux"


def main():
    print("=" * 60)
    print("  GhostStream Build Script")
    print(f"  Platform: {get_platform()}")
    print("=" * 60)

    # Change to script directory
    os.chdir(ROOT)

    # Check pyinstaller
    try:
        import PyInstaller
        print(f"PyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    if not ENTRYPOINT.exists():
        print(f"Missing entrypoint: {ENTRYPOINT}")
        sys.exit(1)

    if not DEFAULT_CONFIG.exists():
        print(f"Missing default config: {DEFAULT_CONFIG}")
        sys.exit(1)

    # Build command
    plat = get_platform()
    separator = ";" if plat == "windows" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=GhostStream",
        "--onefile",
        "--console",
        "--noconfirm",
        "--collect-all=ghoststream",
        "--collect-all=gevent",
        "--collect-all=gevent_websocket",
        "--collect-all=flask",
        "--collect-all=textual",
        "--collect-all=rich",
        "--collect-all=pydantic",
        "--collect-all=pydantic_core",
        "--collect-all=pydantic_settings",
        "--collect-all=httpx",
        "--collect-all=zeroconf",
        "--hidden-import=gevent.monkey",
        "--hidden-import=flask.logging",
        "--hidden-import=flask.sessions",
        "--hidden-import=flask.templating",
        "--hidden-import=flask.blueprints",
        "--hidden-import=flask.json",
        "--hidden-import=gevent.ssl",
        "--hidden-import=gevent_websocket.handler",
        "--hidden-import=gevent_websocket.server",
        "--hidden-import=psutil",
        "--hidden-import=zeroconf",
        "--hidden-import=zeroconf._utils",
        "--hidden-import=httpx",
        "--hidden-import=httpcore",
        "--hidden-import=yaml",
        "--hidden-import=json",
        "--hidden-import=logging.handlers",
        f"--add-data={DEFAULT_CONFIG}{separator}.",
        str(ENTRYPOINT),
    ]

    print("\nBuilding executable...")
    print("Launcher delegates to ghoststream.__main__.main")
    print(f"Command: pyinstaller ... {ENTRYPOINT.relative_to(ROOT)}")
    print()

    result = subprocess.run(cmd)

    if result.returncode == 0:
        if plat == "windows":
            exe_path = Path("dist/GhostStream.exe")
        else:
            exe_path = Path("dist/GhostStream")

        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print()
            print("=" * 60)
            print(f"  BUILD SUCCESS!")
            print(f"  Output: {exe_path.absolute()}")
            print(f"  Size: {size_mb:.1f} MB")
            print("=" * 60)
            print()
            print("To test, run:")
            if plat == "windows":
                print(f"  .\\dist\\GhostStream.exe")
            else:
                print(f"  ./dist/GhostStream")
        else:
            print("Build completed but executable not found?")
    else:
        print()
        print("BUILD FAILED - check errors above")
        sys.exit(1)


if __name__ == "__main__":
    main()
