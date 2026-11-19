#!/usr/bin/env bash
# LTFM Student Dashboard - start on macOS / Linux:  ./run_dashboard.sh
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First start: creating a Python environment and installing packages..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m streamlit run app.py
