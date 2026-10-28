"""Regressions from the 2026-10-28 review: crashes and stale REVIEW reasons, and the cross-site request guard."""
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AUTH", "off")   # these test the API itself; sign-in is tested in test_auth.py
os.environ.setdefault("MODEL_POOL", "off")   # unit tests never load a model
os.environ.setdefault("DOC_PARSER", "off")

from fastapi.testclient import TestClient  # noqa: E402

from invoice_extractor import checks, identifiers, profiles, service  # noqa: E402


class _LeapDay(date):
    @classmethod
    def today(cls):
        return cls(2028, 2, 29)


class ReviewFixes(unittest.TestCase):
    def test_date_check_on_leap_day(self):
        with patch.object(identifiers, "date", _LeapDay):
            self.assertEqual(identifiers.validate("date", "01-01-2026"), "")

    def test_table_without_columns_does_not_crash(self):
        self.assertEqual(checks.run_automatic([{"columns": [], "rows": [["Total", "5"]]}]), [])

    def test_reread_agreement_clears_profile_reason(self):
        res = {"fields": {"invoice_no": {"value": "A1", "status": "unverified", "boxes": [{"page": 1, "bbox": [0, 0, 1, 1]}],
                                         "issue": "read with low OCR confidence (0.93); check it on the page"}},
               "reasons": ["invoice_no: read with low OCR confidence (0.93); check it on the page"]}
        with patch.object(profiles, "_crop", lambda *a: b""):
            out = profiles.reread("x.png", res, "m", read=lambda *a: "A1")
        self.assertEqual(out["reasons"], [])

    def test_cross_origin_write_refused(self):
        c = TestClient(service.app, raise_server_exceptions=False)
        self.assertEqual(c.post("/api/updates/auto", headers={"Origin": "https://evil.example"}, json={}).status_code, 403)
        self.assertEqual(c.get("/health", headers={"Host": "evil.example"}).status_code, 403)
        # the UI dev server proxies from another local port: its writes go through
        self.assertNotEqual(c.post("/api/profiles/nope/models", headers={"Origin": "http://localhost:5173", "Host": "127.0.0.1:8001"},
                                   json={}).status_code, 403)
        self.assertEqual(c.post("/api/updates/auto", headers={"Origin": "null"}, json={}).status_code, 403)
        self.assertNotEqual(c.get("/health").status_code, 403)

    def test_security_headers(self):
        c = TestClient(service.app, raise_server_exceptions=False)
        r = c.get("/health")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["x-frame-options"], "DENY")

    def test_fuzzy_locate_still_matches_ocr_noise(self):
        from invoice_extractor import pipeline
        from invoice_extractor.layout import Unit

        _, index = pipeline.serialize([(1, [Unit("HANDL1NG", (0, 0, 50, 10)), Unit("CHARGES", (55, 0, 99, 10))])])
        hits = pipeline.locate("HANDLING CHARGES", list(index.values()))
        self.assertEqual([h["match"] for h in hits], ["fuzzy"])

    def test_settings_file_is_owner_only_and_single_line_values(self):
        import os
        import stat
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, patch.object(service, "_ENV_LOCAL", Path(tmp, ".env.local")):
            service._write_env_local(["DATABASE_URL=postgres://u:secret@h/db"])
            self.assertEqual(stat.S_IMODE(os.stat(service._ENV_LOCAL).st_mode), 0o600)
            self.assertEqual(os.listdir(tmp), [".env.local"])
            c = TestClient(service.app, raise_server_exceptions=False)
            r = c.post("/api/db/configure", json={"url": "postgres://h/db\nENGINE_AUTO_UPDATE=1"})
            self.assertEqual(r.status_code, 400)
            self.assertNotIn("ENGINE_AUTO_UPDATE", service._ENV_LOCAL.read_text())

    def test_production_hardening(self):
        c = TestClient(service.app, raise_server_exceptions=False)
        self.assertEqual(c.get("/docs").status_code, 404)                      # API docs off unless API_DOCS=1
        self.assertEqual(c.get("/openapi.json").status_code, 404)
        r = c.get("/")
        if r.status_code == 200:
            self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertIn("geolocation=()", c.get("/health").headers["permissions-policy"])
        big = c.post("/api/batches", headers={"content-length": str(service.MAX_REQUEST_BYTES + 1)}, content=b"x")
        self.assertEqual(big.status_code, 413)


if __name__ == "__main__":
    unittest.main()
