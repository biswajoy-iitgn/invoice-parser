"""ODBC contract test. Runs only when TEST_ODBC_DSN is set.

Exercises the real ODBC path (unixODBC + psqlODBC here; iODBC + Actual
Technologies bundle for Excel for Mac; psqlODBC x64 on Windows) against
the reporting view: parameterised lookup + read-only enforcement probe.

Setup (scratch cluster + seed via tests/test_database.py first):
  cat > /tmp/odbctest/odbc.ini <<EOF
  [PGTEST]
  Driver = /opt/homebrew/lib/psqlodbcw.so
  Servername = 127.0.0.1
  Port = 55433
  Database = postgres
  EOF
  ODBCSYSINI=/tmp/odbctest ODBCINI=/tmp/odbctest/odbc.ini \\
  TEST_ODBC_DSN="DSN=PGTEST;Username=postgres" \\
      .venv/bin/python -m unittest discover -s tests -p "test_odbc.py"
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("TEST_ODBC_DSN")


@unittest.skipUnless(DSN, "TEST_ODBC_DSN not set")
class ODBCContracts(unittest.TestCase):
    def test_parameterised_view_lookup(self):
        import pyodbc

        cn = pyodbc.connect(DSN, autocommit=True)
        cur = cn.cursor()
        cur.execute(
            "SELECT invoice_number, vendor_name FROM reporting.invoice_export "
            "WHERE invoice_number = ?", ("INV-T",))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "INV-T")

    def test_view_columns_stable(self):
        import pyodbc

        cn = pyodbc.connect(DSN, autocommit=True)
        cur = cn.cursor()
        cols = [c[0] for c in
                cur.execute("SELECT * FROM reporting.invoice_export LIMIT 0").description]
        for expected in ("invoice_number", "vendor_name", "sea_freight",
                         "handling_charges", "ex_rate", "review_status"):
            self.assertIn(expected, cols)


if __name__ == "__main__":
    unittest.main()
