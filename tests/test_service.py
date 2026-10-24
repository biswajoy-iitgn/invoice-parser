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
        for key in ("pipeline", "reader_backend", "status"):
            self.assertIn(key, body)
        self.assertIn("ocr" if body["pipeline"] != "legacy" else "ingest_mode", body)
        self.assertIn(r.status_code, (200, 503))

    def test_extract_rejects_empty(self):
        r = _client().post("/extract", files={"file": ("a.pdf", b"", "application/pdf")})
        self.assertEqual(r.status_code, 400)

    def test_extract_rejects_oversize(self):
        with patch.object(service, "MAX_UPLOAD_BYTES", 10):
            r = _client().post("/extract", files={"file": ("a.pdf", b"x" * 11, "application/pdf")})
        self.assertEqual(r.status_code, 413)

    def test_extract_rejects_bad_type(self):
        r = _client().post("/extract", files={"file": ("a.exe", b"MZ" + b"x" * 20, "application/octet-stream")})
        self.assertEqual(r.status_code, 415)

    def test_extract_rejects_fake_pdf(self):
        r = _client().post("/extract", files={"file": ("a.pdf", b"not a pdf at all....", "application/pdf")})
        self.assertEqual(r.status_code, 422)

    def test_extract_success_envelope(self):
        fields = {"Invoice No": "INV-1", "Vendor Name": "Acme"}
        audit = {"needs_review": False, "warnings": [], "evidence": "x" * 5000,
                 "timing_sec": {"ingest": 0.1, "llm": 0.2}}

        def fake_run(path, model=None):
            return fields, audit

        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        with patch("invoice_extractor.mineru_engine.extract", fake_run):
            r = _client().post("/extract", files={"file": ("a.png", png, "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["fields"], fields)
        self.assertNotIn("evidence", body["audit"])
        self.assertEqual(body["audit"]["evidence_chars"], 5000)
        self.assertIn("total_s", body["timing"])

    def test_extract_backend_failure_is_502(self):
        def boom(path, model=None):
            raise RuntimeError("reader down")

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        with patch("invoice_extractor.mineru_engine.extract", boom):
            r = _client().post("/extract", files={"file": ("a.png", png, "image/png")})
        self.assertEqual(r.status_code, 502)

    def test_index_serves_ui(self):
        r = _client().get("/")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/html", r.headers["content-type"])
        for token in ("Invoice Extractor", 'id="drop"', 'id="rows"', 'id="items"', 'id="queue"',
                      'id="pages"', "Line items", "/api/batches", "multiple", "/api/semantic/feedback",
                      'id="sheetDb"', "downloadBatch", "foldResults", "openPicker",
                      "No picker?", "Database", "/database", "/settings", 'id="sysline"'):
            self.assertIn(token, r.text)
        # removed: the redundant sheet invoice link and the dead v2 detail cards
        for token in ('id="sheetInvoice"', "Details, arranged", "Document as parsed",
                      "Everything read from the document", "renderFacts", "renderParsed",
                      "renderDetails", "location.href"):
            self.assertNotIn(token, r.text)
        # file inputs must stay rendered (Safari ignores clicks on display:none inputs);
        # the rule lives in the shared stylesheet now, wired via the picker class.
        css = _client().get("/ui/app.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn('input[type="file"].picker', css.text)
        self.assertNotIn("display: none", css.text.split('input[type="file"].picker')[1].split("}")[0])
        self.assertIn('type="file" class="picker"', r.text)
        self.assertNotIn('input[type="file"] { display: none; }', r.text)

    def test_audit_page_serves_trail_ui(self):
        r = _client().get("/audit")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("Audit trail", r.text)
        self.assertIn("/api/audit/verify", r.text)
        self.assertIn("/api/audit/invoice/", r.text)

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
        with patch.dict(os.environ, {}, clear=True):
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
        with patch.dict(os.environ, {}, clear=True):
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
        self.assertTrue(pkgs["docling"]["update_available"])
        self.assertEqual(pkgs["docling"]["latest"], "9.9.9")
        self.assertIn("auto_update", r.json())

    def test_updates_labels_and_mineru_version_display(self):
        import subprocess
        from types import SimpleNamespace

        from invoice_extractor import mineru_engine
        proc = SimpleNamespace(returncode=0, stdout="MinerU version: 4.0.10\n", stderr="")
        with patch.object(service, "_pypi_latest", return_value="9.9.9"), \
                patch.object(service, "_installed_version", return_value="1.0.0"), \
                patch.object(mineru_engine, "resolve_bin", return_value="/bin/mineru"), \
                patch.object(subprocess, "run", return_value=proc):
            service._UPDATES_CACHE.clear()
            self.addCleanup(service._UPDATES_CACHE.clear)
            r = _client().get("/api/updates")
        self.assertEqual(r.status_code, 200, r.text)
        pkgs = {p["name"]: p for p in r.json()["packages"]}
        self.assertEqual(pkgs["sentence-transformers"]["role"], "Embedding layer")
        self.assertEqual(pkgs["mineru"]["current"], "4.0.10")

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
            r = _client().post("/api/updates/apply", json={"packages": ["docling"]})
            self.assertEqual(r.status_code, 202, r.text)
            self.assertTrue(r.json()["started"])
            self.assertTrue(r.json()["restart_required"])
            for _ in range(200):  # background thread finishes the mocked pip promptly
                if not service._UPGRADE_JOB["running"]:
                    break
                time.sleep(0.01)
        self.assertFalse(service._UPGRADE_JOB["running"])
        self.assertEqual(service._UPGRADE_JOB["returncode"], 0)
        self.assertEqual(run.call_args[0][0],
                         [sys.executable, "-m", "pip", "install", "-U", "docling"])

    def test_updates_apply_refuses_busy(self):
        with patch.object(service, "_updates_busy", return_value="batch b1"):
            r = _client().post("/api/updates/apply", json={"packages": ["docling"]})
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
        with patch.dict(os.environ, {}, clear=True):
            r = _client().get("/api/db/schema")
        self.assertEqual(r.status_code, 409)

    def test_settings_page_serves(self):
        r = _client().get("/settings")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn('id="dbcard"', r.text)
        self.assertIn("/api/db/status", r.text)
        self.assertIn('id="updates"', r.text)

    def test_database_page_serves(self):
        r = _client().get("/database")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("/api/db/schema", r.text)

    def test_batch_downloads_and_persist_report_errors_as_json(self):
        c = _client()
        x = c.get("/api/batches/no-such-batch/export.xlsx")
        self.assertEqual(x.status_code, 404)
        p = c.post("/api/batches/no-such-batch/persist")
        self.assertEqual(p.status_code, 404)
        self.assertIn("detail", p.json())  # FastAPI capital-D shape the UI reads

    def test_export_returns_workbook(self):
        import openpyxl

        r = _client().post("/api/export", json={
            "filename": "INV-9.pdf",
            "fields": {"Invoice No": "INV-9", "Vendor Name": "Acme", "Misc": ""},
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("spreadsheetml", r.headers["content-type"])
        self.assertIn('filename="INV-9.xlsx"', r.headers["content-disposition"])
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        rows = {(c[0].value, c[1].value) for c in wb["Invoice"].iter_rows(min_row=2)}
        self.assertIn(("Invoice No", "INV-9"), rows)
        self.assertIn(("Vendor Name", "Acme"), rows)

    def test_export_rejects_empty_fields(self):
        r = _client().post("/api/export", json={"fields": {}})
        self.assertEqual(r.status_code, 400)
        r = _client().post("/api/export", json={})
        self.assertEqual(r.status_code, 400)

    def _valid_png(self, w=60, h=40):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (w, h), "white").save(buf, "PNG")
        return buf.getvalue()

    def test_extract_returns_display_pages(self):
        fields = {"Invoice No": "INV-1"}
        audit = {"needs_review": False, "warnings": [], "evidence": "abc",
                 "timing_sec": {}, "line_items": [],
                 "citations": {"Invoice No": [{"bbox": [1, 2, 10, 12], "page": 1,
                                              "score": 1.0, "source_text": "INV-1",
                                              "source": "tesseract"}]},
                 "line_item_citations": []}

        def fake_run(path, model=None):
            return fields, audit

        with patch("invoice_extractor.mineru_engine.extract", fake_run):
            r = _client().post("/extract",
                               files={"file": ("a.png", self._valid_png(), "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        pages = r.json()["audit"]["pages"]
        self.assertEqual(len(pages), 1)
        self.assertEqual((pages[0]["width"], pages[0]["height"]), (60, 40))
        # Served back to the viewer.
        img = _client().get(pages[0]["url"])
        self.assertEqual(img.status_code, 200)
        self.assertIn("image/png", img.headers["content-type"])

    def test_batch_processes_many_files_and_exports(self):
        import time

        import openpyxl

        calls = []

        def fake_run(path, model=None):
            calls.append(path)
            with open(path, "rb") as fh:
                bad = "bad" in fh.read().decode("latin-1")
            if bad:
                raise RuntimeError("reader down")
            return ({"Invoice No": f"INV-{len(calls)}", "Vendor Name": "Acme", "Sea Freight": "100.00"},
                    {"needs_review": True, "review_route": "REVIEW", "warnings": ["w"], "evidence": "",
                     "line_items": [{"description": "O/F", "amount_inr": 100.0, "category": "Sea Freight",
                                     "category_confidence": 0.99, "semantic": {"basis": "laya"}}],
                     "citations": {}, "line_item_citations": [], "fields_explained": {}})

        from PIL import Image

        def png(color, tag=b""):
            buf = io.BytesIO()
            Image.new("RGB", (40, 30), color).save(buf, "PNG")
            return buf.getvalue() + tag

        files = [("files", ("a.png", png("white"), "image/png")),
                 ("files", ("b.png", png("red"), "image/png")),
                 ("files", ("a_copy.png", png("white"), "image/png")),        # duplicate of a.png
                 ("files", ("c.png", png("blue", b"bad"), "image/png"))]      # extraction fails
        import tempfile
        from pathlib import Path

        from invoice_extractor import batch as batch_mod

        # finished batches are reloaded from disk (the duplicate cache survives restarts), so give this
        # test its own empty batch directory
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(setattr, service, "_BATCHES", None)
        service._BATCHES = None
        with patch.object(batch_mod, "BATCH_DIR", Path(tmp.name)), patch("invoice_extractor.mineru_engine.extract", fake_run):
            c = _client()
            r = c.post("/api/batches", files=files)
            self.assertEqual(r.status_code, 200, r.text)
            bid = r.json()["batch_id"]
            for _ in range(100):
                st = c.get(f"/api/batches/{bid}").json()
                if st["finished"]:
                    break
                time.sleep(0.05)
            self.assertTrue(st["finished"], st)
            self.assertEqual(st["total"], 4)
            self.assertEqual(st["counts"].get("done"), 3)
            self.assertEqual(st["counts"].get("failed"), 1)
            self.assertEqual(len(calls), 3, "identical files must be extracted once")
            dup = [i for i in st["items"] if i["filename"] == "a_copy.png"][0]
            self.assertTrue(dup["duplicate_of"])
            item = c.get(f"/api/batches/{bid}/items/{dup['id']}").json()
            self.assertEqual(item["fields"]["Vendor Name"], "Acme")
            x = c.get(f"/api/batches/{bid}/export.xlsx")
        self.assertEqual(x.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(x.content))
        self.assertEqual(wb.sheetnames, ["Invoices", "Line items", "Citations", "Review"])
        self.assertEqual(wb["Invoices"].max_row, 4)      # header + 3 done invoices
        self.assertEqual(wb["Line items"].max_row, 4)

    def test_semantic_feedback_teaches_alias(self):
        import tempfile

        from invoice_extractor import semantic

        with tempfile.TemporaryDirectory() as d:
            bank = semantic.AliasBank(learned_path=Path(d) / "learned.json")
            eng = semantic.SemanticEngine(bank=bank, encoder_dir="/nonexistent", laya_dir="/nonexistent")
            with patch.object(semantic, "_ENGINE", eng):
                r = _client().post("/api/semantic/feedback",
                                   json={"kind": "charge", "text": "Hamali Chgs", "field": "Handling Charges"})
                self.assertEqual(r.status_code, 200, r.text)
                d0 = eng.classify([{"kind": "charge", "text": "HAMALI CHGS."}])[0]
            self.assertEqual(d0.field, "Handling Charges")
            self.assertIn("alias-bank", d0.votes)
            r = _client().post("/api/semantic/feedback", json={"kind": "charge", "text": "x", "field": "Bogus"})
            self.assertEqual(r.status_code, 400)

    def test_uploads_rejects_traversal(self):
        for bad in ("..%2fservice.py", "....//x.png", "a b.png", "x.txt"):
            r = _client().get(f"/uploads/{bad}")
            self.assertIn(r.status_code, (404, 422), bad)


if __name__ == "__main__":
    unittest.main()
