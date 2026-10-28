"""Service-layer contracts: health/ready/metrics + upload validation.

The heavy backend (default engine ``mineru_engine.extract``) is stubbed; these tests pin the
HTTP envelope, status codes and validation gates.
"""

import asyncio
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AUTH", "off")   # these test the API itself; sign-in is tested in test_auth.py
os.environ.setdefault("MODEL_POOL", "off")   # unit tests never load a model
os.environ.setdefault("DOC_PARSER", "off")

from fastapi.testclient import TestClient  # noqa: E402

from invoice_extractor import service  # noqa: E402


def _client():
    return TestClient(service.app, raise_server_exceptions=False)


class _FakeCur:
    """Stand-in for a psycopg cursor. Either a fixed table (cols/rows + membership
    flag) or a scripted fetch sequence (lists for fetchall, dicts for fetchone)."""

    def __init__(self, cols=(), rows=(), member=True, script=None):
        from types import SimpleNamespace

        self.description = [SimpleNamespace(name=c) for c in cols]
        self._rows = rows
        self._member = member
        self._script = list(script or [])
        self._q = ""

    def execute(self, q, *args):
        self._q = q if isinstance(q, str) else ""

    def fetchone(self):
        if self._script:
            r = self._script.pop(0)
            return r if isinstance(r, dict) else None
        return {"x": 1} if ("information_schema" in self._q and self._member) else None

    def fetchall(self):
        if self._script:
            r = self._script.pop(0)
            return r if isinstance(r, list) else []
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, cols=(), rows=(), member=True, script=None):
        self._cur = _FakeCur(cols, rows, member, script)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cur


class ServiceContracts(unittest.TestCase):

    def test_health(self):
        r = _client().get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})
        self.assertIn("X-Request-ID", r.headers)

    def test_metrics_exposition(self):
        r = _client().get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("invoice_requests_total", r.text)

    def test_ready_reports_backends(self):
        r = _client().get("/ready")
        body = r.json()
        for key in ("ocr", "reader", "table_reader", "models", "sign_in", "status"):
            self.assertIn(key, body)
        self.assertEqual(body["models"]["loaded"], [])                 # readiness never starts a model
        self.assertIn(r.status_code, (200, 503))

    def test_upload_rejects_empty(self):
        r = _client().post("/api/profiles/general/extract", files={"file": ("a.pdf", b"", "application/pdf")})
        self.assertEqual(r.status_code, 400)

    def test_upload_rejects_oversize(self):
        with patch.object(service, "MAX_UPLOAD_BYTES", 10):
            r = _client().post("/api/profiles/general/extract", files={"file": ("a.pdf", b"x" * 11, "application/pdf")})
        self.assertEqual(r.status_code, 413)

    def test_upload_rejects_bad_type(self):
        r = _client().post("/api/profiles/general/extract", files={"file": ("a.exe", b"MZ" + b"x" * 20, "application/octet-stream")})
        self.assertEqual(r.status_code, 415)

    def test_upload_rejects_fake_pdf(self):
        r = _client().post("/api/profiles/general/extract", files={"file": ("a.pdf", b"not a pdf at all....", "application/pdf")})
        self.assertEqual(r.status_code, 422)


    def test_db_status_never_leaks_password(self):
        env = {"DATABASE_URL": "postgres://secretuser:secretpass@127.0.0.1:1/db",
               "INVOICE_ORG_ID": "no-such-org"}
        with patch.dict(os.environ, env):
            r = _client().get("/api/db/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["configured"])
        self.assertNotIn("secretpass", r.text)
        self.assertNotIn("secretuser", r.text)
        self.assertEqual(body["host"], "postgres://127.0.0.1:1")
        self.assertEqual(service._masked_host("postgres:///invoice_demo?host=/tmp"), "this computer")

    def test_db_test_rejects_bad_scheme(self):
        r = _client().post("/api/db/test", json={"url": "http://x", "org": "y"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["reachable"])

    def test_db_test_needs_url(self):
        with patch.dict(os.environ, {"AUTH": "off"}, clear=True):
            r = _client().post("/api/db/test", json={})
        self.assertEqual(r.status_code, 400)

    def test_db_configure_validates_then_saves(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env_path = Path(tmp.name) / ".env.local"
        env_path.write_text("OTHER=keep\nDATABASE_URL=postgres://old@localhost/old\n")
        probe = {"reachable": True, "host": "postgres://db", "organization": "Acme", "org_found": True}
        with patch.object(service, "_try_db", return_value=probe), \
                patch.object(service, "_ENV_LOCAL", env_path):
            r = _client().post("/api/db/configure",
                               json={"url": "postgres://new@localhost/new", "org": "org-1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["saved"])
        self.assertNotIn("postgres://new", r.text)  # URL never echoed
        text = env_path.read_text()
        self.assertIn("OTHER=keep", text)
        self.assertIn("DATABASE_URL=postgres://new@localhost/new", text)
        self.assertIn("INVOICE_ORG_ID=org-1", text)
        self.assertEqual(oct(env_path.stat().st_mode & 0o777), "0o600")

    def test_db_configure_url_only(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env_path = Path(tmp.name) / ".env.local"
        probe = {"reachable": True, "host": "postgres://db"}
        with patch.object(service, "_try_db", return_value=probe), \
                patch.object(service, "_ENV_LOCAL", env_path):
            r = _client().post("/api/db/configure", json={"url": "postgres://new@localhost/new"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["saved"])
        self.assertIsNone(r.json()["organization"])
        text = env_path.read_text()
        self.assertIn("DATABASE_URL=postgres://new@localhost/new", text)
        self.assertNotIn("INVOICE_ORG_ID", text)

    def test_db_preview_rejects_unknown_table(self):
        import database.db as dbmod

        with patch.dict(os.environ, {"DATABASE_URL": "postgres://x"}), \
                patch.object(dbmod, "connect", return_value=_FakeConn(member=False)):
            r = _client().get("/api/db/tables/__nope__")
        self.assertEqual(r.status_code, 404)

    def test_db_preview_needs_config(self):
        with patch.dict(os.environ, {"AUTH": "off"}, clear=True):
            r = _client().get("/api/db/tables/invoices")
        self.assertEqual(r.status_code, 409)

    def test_db_preview_returns_sheet(self):
        import database.db as dbmod

        with patch.dict(os.environ, {"DATABASE_URL": "postgres://x"}), \
                patch.object(dbmod, "connect",
                             return_value=_FakeConn(["id", "invoice_number"],
                                                    [("1", "INV-1"), ("2", None)])):
            r = _client().get("/api/db/tables/invoices")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["columns"], ["id", "invoice_number"])
        self.assertEqual(body["rows"], [["1", "INV-1"], ["2", None]])

    def test_db_configure_rejects_unreachable(self):
        probe = {"reachable": False, "host": "postgres://db", "error": "Connection refused"}
        with patch.object(service, "_try_db", return_value=probe):
            r = _client().post("/api/db/configure", json={"url": "postgres://db/x", "org": "o"})
        self.assertEqual(r.status_code, 502)

    def test_updates_report_available(self):
        with patch.object(service, "_pypi_latest", return_value="9.9.9"), \
                patch.object(service, "_installed_version", return_value="1.0.0"):
            service._UPDATES_CACHE.clear()
            self.addCleanup(service._UPDATES_CACHE.clear)
            r = _client().get("/api/updates")
        self.assertEqual(r.status_code, 200, r.text)
        pkgs = {p["name"]: p for p in r.json()["packages"]}
        self.assertEqual(set(pkgs), {"paddleocr", "paddlex", "mlx-vlm"})            # what the current stack runs on
        self.assertTrue(pkgs["paddleocr"]["update_available"])
        self.assertEqual(pkgs["paddleocr"]["latest"], "9.9.9")
        self.assertIn("auto_update", r.json())


    def test_updates_apply_rejects_unknown(self):
        r = _client().post("/api/updates/apply", json={"packages": ["requests"]})
        self.assertEqual(r.status_code, 400)

    def test_updates_apply_starts_background_upgrade_when_idle(self):
        import subprocess
        import time
        from types import SimpleNamespace

        proc = SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with patch.object(service, "_updates_busy", return_value=None), \
                patch.object(subprocess, "run", return_value=proc) as run:
            r = _client().post("/api/updates/apply", json={"packages": ["paddleocr", "mlx-vlm"]})
            self.assertEqual(r.status_code, 202, r.text)
            self.assertTrue(r.json()["started"])
            self.assertTrue(r.json()["restart_required"])
            for _ in range(200):  # background thread finishes the mocked pip promptly
                if not service._UPGRADE_JOB["running"]:
                    break
                time.sleep(0.01)
        self.assertFalse(service._UPGRADE_JOB["running"])
        self.assertEqual(service._UPGRADE_JOB["returncode"], 0)
        # each package is upgraded where it runs: Paddle in its own environment, the model servers in this one
        self.assertEqual([c[0][0] for c in run.call_args_list],
                         [[service._paddle_python(), "-m", "pip", "install", "-U", "paddleocr"], [sys.executable, "-m", "pip", "install", "-U", "mlx-vlm"]])

    def test_updates_apply_refuses_busy(self):
        with patch.object(service, "_updates_busy", return_value="batch b1"):
            r = _client().post("/api/updates/apply", json={"packages": ["paddleocr"]})
        self.assertEqual(r.status_code, 409)

    def test_updates_busy_covers_single_extractions(self):
        with service._track_extraction():
            busy = service._updates_busy()
        self.assertIn("single extraction", busy)

    def test_updates_auto_toggle(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env_path = Path(tmp.name) / ".env.local"
        env_path.write_text("OTHER=1\n")
        with patch.object(service, "_ENV_LOCAL", env_path):
            r = _client().post("/api/updates/auto", json={"enabled": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["auto_update"])
        text = env_path.read_text()
        self.assertIn("OTHER=1", text)
        self.assertIn("ENGINE_AUTO_UPDATE=1", text)

    def test_db_schema_returns_tables_and_links(self):
        import database.db as dbmod

        script = [[{"table_name": "invoices"}],
                  [{"column_name": "id", "data_type": "uuid"}],
                  {"n": 2},
                  [{"table_name": "invoices", "column_name": "vendor_id",
                    "ref_table": "vendors", "ref_column": "id"}]]
        with patch.dict(os.environ, {"DATABASE_URL": "postgres://x"}), \
                patch.object(dbmod, "connect", return_value=_FakeConn(script=script)):
            r = _client().get("/api/db/schema")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["tables"][0]["name"], "invoices")
        self.assertEqual(body["tables"][0]["columns"], [{"name": "id", "type": "uuid"}])
        self.assertEqual(body["tables"][0]["rows"], 2)
        self.assertEqual(body["tables"][0]["friendly"], "Invoices")
        self.assertTrue(body["tables"][0]["main"])
        self.assertIn("look", body["tables"][0]["blurb"])
        self.assertEqual(body["links"], [{"table": "invoices", "column": "vendor_id",
                                          "ref_table": "vendors", "ref_column": "id"}])

    def test_db_schema_needs_config(self):
        with patch.dict(os.environ, {"AUTH": "off"}, clear=True):
            r = _client().get("/api/db/schema")
        self.assertEqual(r.status_code, 409)


    def _valid_png(self, w=60, h=40):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (w, h), "white").save(buf, "PNG")
        return buf.getvalue()


if __name__ == "__main__":
    unittest.main()
