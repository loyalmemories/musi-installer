@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Preparing Musi Installer for the first time...
    python -m venv .venv
    if errorlevel 1 goto error
)
".venv\Scripts\python.exe" -c "import pymobiledevice3, customtkinter" >nul 2>&1
if errorlevel 1 (
    echo Installing the required components. This only happens on first launch.
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto error
)
start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0
:error
echo Setup could not finish. Python 3.10 or newer must be installed and available in PATH.
echo Check the message above, then try again.
pause
exit /b 1
