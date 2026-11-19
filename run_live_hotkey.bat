@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\live_hotkey.py %*
) else (
    py -3.11 tools\live_hotkey.py %*
)
