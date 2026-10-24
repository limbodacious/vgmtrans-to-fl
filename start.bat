@echo off
rem Starts vgmtrans-to-fl. The first run sets up its Python packages (about 300 MB).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo First run: installing the app's packages, this takes a minute...
    python -m venv .venv || (echo Python 3 is needed: https://www.python.org/downloads/ & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (pause & exit /b 1)
)
start "" ".venv\Scripts\pythonw.exe" app.py
