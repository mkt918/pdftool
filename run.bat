@echo off
rem PDF Utility Tool launcher.
rem Note: this .bat intentionally uses English-only text for its own messages.
rem Windows cmd.exe's handling of UTF-8 batch files is unreliable across
rem environments/codepages (verified: Japanese echo lines got corrupted and
rem misparsed as commands even with `chcp 65001`). The application itself
rem (the PySide6 GUI) is fully in Japanese; only this launcher script is not.
rem
rem Drag & drop a PDF onto this file to open it directly.

chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up the environment, this may take a few minutes...
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found. Install Python 3.11 or later and try again.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install required packages. Check your network connection.
        pause
        exit /b 1
    )
    echo Setup complete.
)

set PYTHONPATH=src
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m pdftool %*

if errorlevel 1 (
    echo.
    echo The app exited with an error. See the message above.
    pause
)
endlocal
