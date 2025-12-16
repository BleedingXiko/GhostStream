@echo off
:: GhostStream - Windows One-Click Launcher
:: Just double-click this file to start GhostStream

title GhostStream

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Python not found!
    echo.
    echo  Install Python from: https://python.org/downloads
    echo  Make sure to check "Add Python to PATH" during install
    echo.
    pause
    exit /b 1
)

:: Run the launcher
python "%~dp0run.py"

:: Keep window open if there was an error
if %errorlevel% neq 0 (
    echo.
    echo Press any key to exit...
    pause >nul
)
