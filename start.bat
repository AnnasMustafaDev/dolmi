@echo off
REM Dolmi - one-click start. First run installs everything (several minutes).
cd /d %~dp0
echo [1/3] Checking Python...
python --version || (echo Python not found. Install Python 3.10+ from python.org & pause & exit /b 1)

if not exist venv\Scripts\python.exe (
  echo [2/3] Creating virtual environment...
  python -m venv venv || (echo Could not create venv & pause & exit /b 1)
)

if not exist venv\.installed-v5 (
  echo [2/3] Installing packages - first run only, this can take 5-15 minutes.
  echo       Progress is shown below. Do NOT click inside this window - that pauses it.
  venv\Scripts\python.exe -m pip install --upgrade pip
  venv\Scripts\python.exe -m pip install --progress-bar on -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Install FAILED. If you are on a company network, pip may be blocked by a proxy.
    echo Copy the error above and send it to Claude.
    pause & exit /b 1
  )
  echo ok> venv\.installed-v5
  powershell -NoProfile -ExecutionPolicy Bypass -File create_shortcuts.ps1
)

echo [3/3] Starting Dolmi...
start "" venv\Scripts\pythonw.exe src\main.py
