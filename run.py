#!/usr/bin/env python3
"""
GhostStream - Zero Setup Launcher
Just run: python run.py

Handles everything automatically:
- Creates virtual environment if needed
- Installs dependencies
- Detects FFmpeg (with install instructions if missing)
- Starts the server
"""

import os
import sys
import subprocess
import shutil
import platform
from pathlib import Path

# Colors for terminal output (works on all platforms)
class Colors:
    if sys.platform == "win32":
        # Enable ANSI escape sequences on Windows 10+
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass  # Silently fail on older Windows versions
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"

def print_banner():
    print(f"""
{Colors.BLUE}{Colors.BOLD}
   _____ _               _   _____ _                          
  / ____| |             | | / ____| |                         
 | |  __| |__   ___  ___| || (___ | |_ _ __ ___  __ _ _ __ ___ 
 | | |_ | '_ \\ / _ \\/ __| __\\___ \\| __| '__/ _ \\/ _` | '_ ` _ \\
 | |__| | | | | (_) \\__ \\ |_ ___) | |_| | |  __/ (_| | | | | | |
  \\_____|_| |_|\\___/|___/\\__|____/ \\__|_|  \\___|\\__,_|_| |_| |_|
{Colors.END}
  {Colors.GREEN}Zero-Setup Transcoding Server{Colors.END}
""")

def log(msg, color=Colors.GREEN):
    print(f"{color}▸{Colors.END} {msg}")

def log_error(msg):
    print(f"{Colors.RED}✗ {msg}{Colors.END}")

def log_success(msg):
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")

def log_warn(msg):
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.END}")

def get_python_cmd():
    """Get the right Python command for this system."""
    # Try python3 first (Linux/macOS), then python (Windows)
    for cmd in ["python3", "python"]:
        try:
            result = subprocess.run(
                [cmd, "--version"], 
                capture_output=True, 
                text=True
            )
            if result.returncode == 0 and "3." in result.stdout:
                return cmd
        except FileNotFoundError:
            continue
    return None

def check_python():
    """Ensure Python 3.10+ is available."""
    log("Checking Python version...")
    
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        log_error(f"Python 3.10+ required, found {version.major}.{version.minor}")
        print(f"\n  Install Python 3.10+ from: {Colors.BLUE}https://python.org/downloads{Colors.END}\n")
        return False
    
    log_success(f"Python {version.major}.{version.minor}.{version.micro}")
    return True

def check_ffmpeg():
    """Check if FFmpeg is installed and provide install instructions if not."""
    log("Checking FFmpeg...")
    
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        # Get version
        try:
            result = subprocess.run(
                [ffmpeg, "-version"], 
                capture_output=True, 
                text=True
            )
            version_line = result.stdout.split("\n")[0]
            log_success(f"FFmpeg found: {version_line[:50]}...")
            return True
        except Exception:
            log_success(f"FFmpeg found at {ffmpeg}")
            return True
    
    log_error("FFmpeg not found!")
    print(f"\n{Colors.YELLOW}Install FFmpeg:{Colors.END}\n")
    
    system = platform.system().lower()
    if system == "windows":
        print(f"""  {Colors.BOLD}Option 1 - winget (recommended):{Colors.END}
    winget install FFmpeg
    
  {Colors.BOLD}Option 2 - Chocolatey:{Colors.END}
    choco install ffmpeg
    
  {Colors.BOLD}Option 3 - Manual:{Colors.END}
    1. Download from https://www.gyan.dev/ffmpeg/builds/
    2. Extract to C:\\ffmpeg
    3. Add C:\\ffmpeg\\bin to your PATH
""")
    elif system == "darwin":
        print(f"""  {Colors.BOLD}Homebrew (recommended):{Colors.END}
    brew install ffmpeg
    
  {Colors.BOLD}MacPorts:{Colors.END}
    sudo port install ffmpeg
""")
    else:  # Linux
        print(f"""  {Colors.BOLD}Ubuntu/Debian:{Colors.END}
    sudo apt update && sudo apt install ffmpeg
    
  {Colors.BOLD}Fedora:{Colors.END}
    sudo dnf install ffmpeg
    
  {Colors.BOLD}Arch:{Colors.END}
    sudo pacman -S ffmpeg
""")
    
    print(f"  After installing, run this script again.\n")
    return False

def setup_venv():
    """Create virtual environment if it doesn't exist."""
    venv_path = Path(__file__).parent / "venv"
    
    if venv_path.exists():
        log_success("Virtual environment exists")
        return venv_path
    
    log("Creating virtual environment...")
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            check=True,
            capture_output=True
        )
        log_success("Virtual environment created")
        return venv_path
    except subprocess.CalledProcessError as e:
        log_error(f"Failed to create venv: {e}")
        return None

def get_venv_python(venv_path):
    """Get the Python executable inside the venv."""
    if sys.platform == "win32":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"

def get_venv_pip(venv_path):
    """Get the pip executable inside the venv."""
    if sys.platform == "win32":
        return venv_path / "Scripts" / "pip.exe"
    return venv_path / "bin" / "pip"

def install_dependencies(venv_path):
    """Install dependencies if needed."""
    pip = get_venv_pip(venv_path)
    requirements = Path(__file__).parent / "requirements.txt"
    
    # Check if ghoststream is already installed
    python = get_venv_python(venv_path)
    try:
        result = subprocess.run(
            [str(python), "-c", "import ghoststream"],
            capture_output=True
        )
        if result.returncode == 0:
            log_success("Dependencies already installed")
            return True
    except Exception:
        pass
    
    log("Installing dependencies (this may take a minute)...")
    try:
        # Upgrade pip first
        subprocess.run(
            [str(pip), "install", "--upgrade", "pip"],
            capture_output=True,
            check=True
        )
        
        # Install requirements
        subprocess.run(
            [str(pip), "install", "-r", str(requirements)],
            check=True
        )
        log_success("Dependencies installed")
        return True
    except subprocess.CalledProcessError as e:
        log_error(f"Failed to install dependencies: {e}")
        return False

def run_ghoststream(venv_path):
    """Run GhostStream."""
    python = get_venv_python(venv_path)
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}Starting GhostStream...{Colors.END}\n")
    
    try:
        # Run in the project directory
        os.chdir(Path(__file__).parent)
        subprocess.run([str(python), "-m", "ghoststream"])
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Shutting down...{Colors.END}")

def main():
    print_banner()
    
    # Check prerequisites
    if not check_python():
        sys.exit(1)
    
    if not check_ffmpeg():
        sys.exit(1)
    
    # Setup environment
    venv_path = setup_venv()
    if not venv_path:
        sys.exit(1)
    
    # Install dependencies
    if not install_dependencies(venv_path):
        sys.exit(1)
    
    # Run
    run_ghoststream(venv_path)

if __name__ == "__main__":
    main()
