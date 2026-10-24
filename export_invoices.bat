@echo off
rem One command (Windows): all invoices from the database -> exports\invoices_<time>.xlsx
rem   export_invoices.bat                          every invoice
rem   export_invoices.bat --from 2026-04-01 --vendor dachser
rem Needs Python 3.10+ (python.org). The database address is read from .env.local next to this file
rem (DATABASE_URL_READONLY=... or DATABASE_URL=...), or from the environment.
setlocal
cd /d "%~dp0"
set "PY=.venv-export\Scripts\python.exe"
if not exist "%PY%" (
  py -3 -m venv .venv-export 2>nul || python -m venv .venv-export
  if errorlevel 1 ( echo Python 3 was not found. Install it from python.org and run this again. & pause & exit /b 1 )
  "%PY%" -m pip install --quiet --upgrade pip
  "%PY%" -m pip install --quiet "psycopg[binary]>=3.2" "openpyxl>=3.1"
  if errorlevel 1 ( echo Could not install the two required packages. & pause & exit /b 1 )
  "%PY%" -m pip install --quiet "pyodbc>=5" 2>nul
)
"%PY%" scripts\export_invoices.py %*
if errorlevel 1 pause
endlocal
