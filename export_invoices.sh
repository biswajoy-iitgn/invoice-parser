#!/bin/sh
# One command (macOS / Linux): all invoices from the database -> exports/invoices_<time>.xlsx
#   ./export_invoices.sh                         every invoice
#   ./export_invoices.sh --from 2026-04-01 --vendor dachser
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  PY=.venv-export/bin/python
  if [ ! -x "$PY" ]; then
    python3 -m venv .venv-export && "$PY" -m pip install --quiet --upgrade pip \
      && "$PY" -m pip install --quiet "psycopg[binary]>=3.2" "openpyxl>=3.1" || exit 1
    "$PY" -m pip install --quiet "pyodbc>=5" 2>/dev/null || true    # optional: psqlODBC route
  fi
fi
exec "$PY" scripts/export_invoices.py "$@"
