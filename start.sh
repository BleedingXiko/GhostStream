#!/bin/bash
# GhostStream - macOS/Linux One-Click Launcher
# Run: ./start.sh (or double-click on macOS)

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Use venv Python if it exists, otherwise fall back to system Python
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo ""
    echo "  ERROR: Python 3 not found!"
    echo ""
    echo "  Install Python:"
    echo "    macOS:  brew install python"
    echo "    Ubuntu: sudo apt install python3 python3-venv"
    echo "    Fedora: sudo dnf install python3"
    echo ""
    exit 1
fi

# Run the launcher
exec $PYTHON run.py
