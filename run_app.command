#!/usr/bin/env bash
# Double-click on a Mac (or run ./run_app.command on Linux) to open the valuation app in your browser.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting things up (about a minute)..."
  PY=$(command -v python3 || command -v python)
  if [ -z "$PY" ]; then
    echo "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again."
    read -r -p "Press Enter to close."
    exit 1
  fi
  "$PY" -m venv .venv || { read -r -p "Could not create the environment. Press Enter to close."; exit 1; }
fi

source .venv/bin/activate
if ! python -c "import streamlit, yfinance, openpyxl" 2>/dev/null; then
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt || { read -r -p "Install failed. Press Enter to close."; exit 1; }
fi

python -m streamlit run app.py
