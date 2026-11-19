@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher was not found. Install Python 3.11 and try again.
    pause
    exit /b 1
)
where node >nul 2>nul
if errorlevel 1 (
    echo Node.js was not found. Install Node.js 20 or newer and try again.
    pause
    exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo ffmpeg was not found on PATH. Install FFmpeg and add its bin folder to PATH.
    pause
    exit /b 1
)
where ffprobe >nul 2>nul
if errorlevel 1 (
    echo ffprobe was not found on PATH. Install FFmpeg and add its bin folder to PATH.
    pause
    exit /b 1
)

if not defined DATA_DIR set "DATA_DIR=C:\1Clipping\data"
if not exist ".venv\Scripts\python.exe" py -3.11 -m venv .venv
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :failed
if not exist "frontend\node_modules" (
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        goto :failed
    )
    popd
)

start "Local Clipper API" cmd /k "cd /d ""%CD%"" && set ""DATA_DIR=%DATA_DIR%"" && "".venv\Scripts\python.exe"" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000"
start "Local Clipper UI" cmd /k "cd /d ""%CD%\frontend"" && npm run dev -- --host 127.0.0.1"
echo Local Clipper is starting. Open http://127.0.0.1:5173
exit /b 0

:failed
echo Setup failed. Check the message above, then try run_local.bat again.
pause
exit /b 1
