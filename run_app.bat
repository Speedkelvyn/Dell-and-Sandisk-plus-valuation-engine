@echo off
rem Double-click to open the valuation app in your browser.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto have_env
echo First run: setting things up, about a minute...
py -3 -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto have_env
python -m venv .venv 2>nul
if exist ".venv\Scripts\python.exe" goto have_env
echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again.
pause
exit /b 1

:have_env
call ".venv\Scripts\activate.bat"
python -c "import streamlit, yfinance, openpyxl" 2>nul
if not errorlevel 1 goto run
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo Install failed. Check your internet connection and try again.
  pause
  exit /b 1
)

:run
python -m streamlit run app.py
pause
