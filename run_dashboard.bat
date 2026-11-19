@echo off
REM LTFM Student Dashboard - double-click to start (Windows)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First start: creating a Python environment and installing packages...
    py -3 -m venv .venv || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" -m streamlit run app.py
pause
