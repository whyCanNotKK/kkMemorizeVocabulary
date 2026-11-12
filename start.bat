@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please create .venv and install requirements.lock.txt first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\update_source.py"
start "" ".venv\Scripts\pythonw.exe" "main.py" %*
