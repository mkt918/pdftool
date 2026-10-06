@echo off
rem Publish a new version: release.bat 0.2.0 "notes"
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\release.py %*
pause
endlocal
