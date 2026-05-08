@echo off
title MyGram Scraper Setup
echo ======================================================
echo    MyGram Instagram Scraper - One-Click Launcher
echo ======================================================
echo.
echo This script will verify your Python environment,
echo install missing dependencies, and launch the scraper.
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b
)

:: Run setup.py
python setup.py

echo.
echo Press any key to exit...
pause >nul
