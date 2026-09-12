@echo off
rem Build a standalone .exe (dist\PDFユーティリティ.exe) using PyInstaller.
rem Note: English-only messages here for the same reason as run.bat
rem (cmd.exe's UTF-8 batch handling is unreliable).

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run run.bat once first to set up the environment.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller into .venv...
    ".venv\Scripts\python.exe" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

echo Building... this can take a minute or two.
".venv\Scripts\pyinstaller.exe" --noconfirm pdftool.spec
if errorlevel 1 (
    echo [ERROR] Build failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo Done. The executable is in the "dist" folder.
pause
endlocal
