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
    os.chdir(Path(__file__).parent)
    
    # Check pyinstaller
    try:
        import PyInstaller
        print(f"PyInstaller: {PyInstaller.__version__}")
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    
    # Build command
    plat = get_platform()
    separator = ";" if plat == "windows" else ":"
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=GhostStream",
        "--onefile",
        "--console",
        "--noconfirm",
        f"--icon=desktop/src-tauri/icons/Ghosthub.ico",
        "--collect-all=ghoststream",
        "--collect-all=uvicorn",
        "--collect-all=starlette",
        "--collect-all=fastapi",
        "--collect-all=pydantic",
        "--collect-all=pydantic_core",
        "--collect-all=pydantic_settings",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.http.h11_impl",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.protocols.websockets.wsproto_impl",
        "--hidden-import=uvicorn.protocols.websockets.websockets_impl",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=uvicorn.lifespan.off",
        "--hidden-import=starlette.responses",
        "--hidden-import=starlette.routing",
        "--hidden-import=starlette.middleware",
        "--hidden-import=starlette.middleware.cors",
        "--hidden-import=starlette.staticfiles",
        "--hidden-import=starlette.templating",
        "--hidden-import=anyio",
        "--hidden-import=anyio._backends",
        "--hidden-import=anyio._backends._asyncio",
        "--hidden-import=sniffio",
        "--hidden-import=h11",
        "--hidden-import=httptools",
        "--hidden-import=dotenv",
        "--hidden-import=yaml",
        "--hidden-import=httpx",
        "--hidden-import=httpcore",
        "--hidden-import=aiofiles",
        "--hidden-import=aiofiles.os",
        "--hidden-import=aiofiles.ospath",
        "--hidden-import=websockets",
        "--hidden-import=websockets.legacy",
        "--hidden-import=websockets.legacy.server",
        "--hidden-import=zeroconf",
        "--hidden-import=zeroconf._utils",
        "--hidden-import=psutil",
        "--hidden-import=multipart",
        "--hidden-import=python_multipart",
        "--hidden-import=email.mime.multipart",
        "--hidden-import=concurrent.futures",
        "--hidden-import=asyncio",
        "--hidden-import=json",
        "--hidden-import=logging.handlers",
        "--hidden-import=typing_extensions",
        "--hidden-import=annotated_types",
        f"--add-data=ghoststream.yaml{separator}.",
        "ghoststream/launcher.py",
    ]
    
    # Add uvloop on non-Windows
    if plat != "windows":
        cmd.insert(-1, "--hidden-import=uvloop")
        cmd.insert(-1, "--hidden-import=uvicorn.loops.uvloop")
        cmd.insert(-1, "--hidden-import=uvicorn.protocols.http.httptools_impl")
    
    print("\nBuilding executable...")
    print(f"Command: pyinstaller ... ghoststream/launcher.py")
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
