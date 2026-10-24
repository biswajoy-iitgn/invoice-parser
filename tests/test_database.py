"""Database contracts. Live tests run only when TEST_DATABASE_URL is set.

Run a scratch cluster, e.g.:
  initdb -D /tmp/pgtest-pgdata -U postgres --auth=trust
  mkdir -p /tmp/pgtest-sock && pg_ctl -D /tmp/pgtest-pgdata -o "-k /tmp/pgtest-sock -p 55433" -l /tmp/pgtest.log start
  TEST_DATABASE_URL="postgres://postgres@127.0.0.1:55433/postgres?host=/tmp/pgtest-sock" \\
      .venv/bin/python -m unittest discover -s tests -p "test_database.py" -v
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(URL, "TEST_DATABASE_URL not set")
class DatabaseContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from database.db import migrate

        os.environ["DATABASE_URL"] = URL
        migrate(URL)

    def _org(self):
        import psycopg

        with psycopg.connect(URL) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO organizations (name) VALUES ('t') RETURNING id")
            return str(cur.fetchone()[0])

    def test_migrations_idempotent(self):
        from database.db import migrate

        self.assertEqual(migrate(URL), [])

    def test_ontology_seed(self):
        import psycopg

        with psycopg.connect(URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM field_definitions")
            self.assertEqual(cur.fetchone()[0], 31)
            cur.execute("SELECT count(*) FROM charge_types")
            self.assertEqual(cur.fetchone()[0], 22)

    def test_document_idempotency(self):
        from database.db import Store

        s = Store(self._org())
        kw = dict(filename="a.pdf", mime="application/pdf", size=1,
                  uri="u", sha256="ff" * 32, pages=1)
        self.assertEqual(s.upsert_document(**kw)["id"], s.upsert_document(**kw)["id"])

    def test_invoice_roundtrip_through_view(self):
        from database.db import Store

        s = Store(self._org())
        d = s.upsert_document(filename="a.pdf", mime="application/pdf", size=1,
                              uri="u", sha256="ee" * 32, pages=1)
        run = s.create_run(document_id=str(d["id"]),
                           pipeline_version="t", model_config=None, input_hash="ee" * 32)
        s.save_invoice(run_id=str(run["id"]), document_id=str(d["id"]),
                       canonical={"invoice_number": "INV-T", "sea_freight": "10.5"},
                       vendor_name="V")
        s.finish_run(str(run["id"]), ok=True, ms=1, final={"fields": {"Ex Rate": "USD 83.50"}})
        rows = s.invoice_by_number("INV-T")
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]["sea_freight"]), 10.5)
        self.assertEqual(rows[0]["ex_rate"], "USD 83.50")

    def test_invoice_reextraction_upserts(self):
        # Re-extracting the same invoice must not raise UniqueViolation:
        # latest run wins, one invoice row, replaced children.
        from database.db import Store

        s = Store(self._org())
        run_ids = []
        for tag, amount in (("aa", "10.5"), ("bb", "20.5")):
            d = s.upsert_document(filename="a.pdf", mime="application/pdf", size=1,
                                  uri="u", sha256=tag * 32, pages=1)
            run = s.create_run(document_id=str(d["id"]),
                               pipeline_version="t", model_config=None,
                               input_hash=tag * 32)
            run_ids.append(str(run["id"]))
            s.save_invoice(run_id=str(run["id"]), document_id=str(d["id"]),
                           canonical={"invoice_number": "INV-T2", "sea_freight": amount},
                           vendor_name="V2")
        rows = s.invoice_by_number("INV-T2")
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]["sea_freight"]), 20.5)

    def test_tenant_isolation(self):
        # RLS is enforced for non-owner roles: verify as a login role, since
        # superusers/table owners bypass RLS by design.
        import psycopg

        from database.db import Store

        o1, o2 = self._org(), self._org()
        Store(o1).upsert_document(filename="a.pdf", mime="application/pdf", size=1,
                                  uri="u", sha256="cc" * 32, pages=1)
        with psycopg.connect(URL) as conn:
            conn.execute("DROP ROLE IF EXISTS rls_probe")
            conn.execute("CREATE ROLE rls_probe LOGIN PASSWORD 'probe'")
            conn.execute("GRANT SELECT ON documents TO rls_probe")
        from urllib.parse import parse_qs, urlparse

        parts = urlparse(URL)
        sock = parse_qs(parts.query).get("host", [""])[0]
        probe_url = (f"postgres://rls_probe:probe@{parts.hostname}:{parts.port}"
                     f"/postgres?host={sock}")
        with psycopg.connect(probe_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, TRUE)", (o2,))
            cur.execute("SELECT count(*) FROM documents")
            self.assertEqual(cur.fetchone()[0], 0)
        with psycopg.connect(probe_url) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org', %s, TRUE)", (o1,))
            cur.execute("SELECT count(*) FROM documents")
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(URL, "TEST_DATABASE_URL not set")
class AuditTrail(unittest.TestCase):
    """Migration 006: the audit log is append-only and hash-chained."""

    @classmethod
    def setUpClass(cls):
        from database.db import migrate

        os.environ["DATABASE_URL"] = URL
        migrate(URL)

    def test_chain_append_only_and_tamper_evident(self):
        import psycopg

        from database.db import Store

        with psycopg.connect(URL) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO organizations (name) VALUES ('audit') RETURNING id")
            org = str(cur.fetchone()[0])
        s = Store(org)
        for n in range(3):
            s.log_audit(entity="invoice", entity_id="00000000-0000-0000-0000-00000000000%d" % n,
                        action="CREATE", new={"n": n})
        with psycopg.connect(URL) as conn:
            checked, bad = conn.execute("SELECT * FROM audit_chain_verify()").fetchone()
            self.assertGreaterEqual(checked, 3)
            self.assertIsNone(bad)
            rows = conn.execute("SELECT seq, prev_hash, row_hash FROM audit_logs ORDER BY seq DESC LIMIT 2").fetchall()
            self.assertEqual(rows[0][1], rows[1][2])           # each entry carries the digest of the one before
        for stmt in ("UPDATE audit_logs SET action = 'DELETE'", "DELETE FROM audit_logs", "TRUNCATE audit_logs"):
            with self.assertRaises(psycopg.Error), psycopg.connect(URL) as conn:
                conn.execute(stmt)
        # an administrator who switches the guard off and edits history is still caught by the chain
        with psycopg.connect(URL) as conn:
            conn.execute("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_change")
            victim = conn.execute("SELECT seq FROM audit_logs ORDER BY seq DESC OFFSET 1 LIMIT 1").fetchone()[0]
            conn.execute("UPDATE audit_logs SET new_json = '{\"n\": 99}' WHERE seq = %s", (victim,))
            conn.execute("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_no_change")
        with psycopg.connect(URL) as conn:
            _, bad = conn.execute("SELECT * FROM audit_chain_verify()").fetchone()
            self.assertEqual(bad, victim)
