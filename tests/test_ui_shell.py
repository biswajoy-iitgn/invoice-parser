"""Application shell contracts: every page shares one header/nav, one stylesheet.

Pins the redesign's core invariant — a single primary navigation with the
correct active item on each page — so future page edits cannot silently drift
back into per-page headers.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from invoice_extractor import service  # noqa: E402

NAV_HREFS = ["/", "/database", "/analytics", "/audit", "/settings"]


def _client():
    return TestClient(service.app, raise_server_exceptions=False)


def _nav(html):
    m = re.search(r'<nav class="app-nav"[^>]*>(.*?)</nav>', html, re.S)
    if not m:
        return None
    return re.findall(r'<a href="([^"]+)"( aria-current="page")?>', m.group(1))


class ShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = _client()

    def _page(self, path):
        r = self.c.get(path)
        self.assertEqual(r.status_code, 200, path)
        self.assertIn("text/html", r.headers["content-type"], path)
        return r.text

    def test_shared_assets_served(self):
        for path, ctype in [("/ui/app.css", "text/css"),
                            ("/ui/icons.svg", "image/svg+xml"),
                            ("/ui/app.js", "javascript")]:
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn(ctype, r.headers["content-type"], path)
            self.assertTrue(r.content, path)

    def test_nav_identical_everywhere_with_one_active_item(self):
        cases = {"/": "/", "/database": "/database",
                 "/database/invoices": "/database",
                 "/audit": "/audit", "/audit/INV-1": "/audit",
                 "/settings": "/settings"}
        for path, active in cases.items():
            with self.subTest(path=path):
                nav = _nav(self._page(path))
                self.assertIsNotNone(nav, f"{path} has no app-nav")
                self.assertEqual([h for h, _ in nav], NAV_HREFS, path)
                current = [h for h, a in nav if a]
                self.assertEqual(current, [active], f"{path} active item")

    def test_pages_share_shell_landmarks(self):
        for path in ["/", "/database", "/audit", "/settings"]:
            with self.subTest(path=path):
                html = self._page(path)
                self.assertIn('<link rel="stylesheet" href="/ui/app.css">', html)
                self.assertIn('class="skip" href="#main"', html)
                self.assertIn("<h1>", html)
                self.assertIn('id="main"', html)

    def test_brand_home_link_on_every_page(self):
        for path in ["/", "/database", "/audit", "/settings"]:
            with self.subTest(path=path):
                self.assertIn('class="brand" href="/"', self._page(path))

    def test_analytics_serves_report_or_shell_fallback(self):
        r = self.c.get("/analytics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_analytics_template_uses_shared_shell(self):
        from analytics import report_html

        nav = _nav(report_html.PAGE)
        self.assertIsNotNone(nav, "analytics template has no app-nav")
        self.assertEqual([h for h, _ in nav], NAV_HREFS)
        current = [h for h, a in nav if a]
        self.assertEqual(current, ["/analytics"])
        for marker in ('<link rel="stylesheet" href="/ui/app.css">',
                       'class="skip" href="#main"', "<h1>", 'id="main"',
                       'class="brand" href="/"'):
            self.assertIn(marker, report_html.PAGE)
        self.assertNotIn("topnav", report_html.PAGE)

    def test_pages_and_shared_assets_are_not_cached(self):
        for path in ["/", "/database", "/database/invoices", "/audit", "/audit/INV-1",
                     "/settings", "/analytics", "/ui/app.css", "/ui/app.js", "/ui/icons.svg"]:
            with self.subTest(path=path):
                r = self.c.get(path)
                self.assertEqual(r.status_code, 200, path)
                self.assertEqual(r.headers.get("cache-control"), "no-store", path)

    def test_evidence_view_skips_empty_parts_and_empty_facts(self):
        import hashlib
        import os
        import tempfile
        from unittest.mock import patch

        invoice_id = "5b9863fd-db02-4b08-ac4a-346d32f28afc"
        page_bytes = b"fake-jpeg-bytes"
        inv = {"invoice_number": "INV-1", "vendor_name": "Acme", "invoice_date": None,
               "review_status": "APPROVED", "updated_at": "2026-10-07 09:41:25",
               "final_output_json": {"fields": {"Invoice No": "INV-1"}, "review_route": "AUTO_ACCEPT"}}
        pages = [{"page_number": 1, "storage_uri": "p1.jpg", "boxes": 3,
                  "created_at": "2026-10-04 00:04:10",
                  "sha256": hashlib.sha256(page_bytes).hexdigest()}]
        facts = [{"page_number": 1, "section": "header", "label": "no", "value": "INV-1",
                  "output_field": "invoice_number"}]

        class _Store:
            def __init__(self, org):
                pass

            def evidence(self, i):
                return {"invoice": inv, "pages": pages}

            def facts(self, i):
                return list(_Store._facts)

            def attributes(self, i):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "p1.jpg").write_bytes(page_bytes)
            with patch("database.db.Store", _Store), \
                    patch("database.persist.evidence_dir", return_value=Path(tmp)), \
                    patch.dict(os.environ, {"DATABASE_URL": "x", "INVOICE_ORG_ID": "o"}):
                _Store._facts = []
                html = self._page(f"/evidence/{invoice_id}")
                self.assertIn("Acme · review status APPROVED · result AUTO_ACCEPT"
                              " · last updated 2026-10-07 09:41:25 UTC", html)
                self.assertNotIn("· ·", html)
                self.assertNotIn("None", html)
                self.assertNotIn("Everything read from the document", html)
                _Store._facts = facts
                html = self._page(f"/evidence/{invoice_id}")
                self.assertIn("Everything read from the document (1 items)", html)


if __name__ == "__main__":
    unittest.main()
