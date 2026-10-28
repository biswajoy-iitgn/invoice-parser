"""Batches read under a profile (the Fluent UI's path), the extraction slots, and batch state under concurrent workers."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AUTH", "off")   # these test the API itself; sign-in is tested in test_auth.py
os.environ.setdefault("MODEL_POOL", "off")   # unit tests never load a model
os.environ.setdefault("DOC_PARSER", "off")

import fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from invoice_extractor import batch as batch_mod, profiles, service  # noqa: E402


def _pdf() -> bytes:
    d = fitz.open()
    d.new_page().insert_text((50, 60), "TAX INVOICE  INV-001")
    return d.tobytes()


class _Reader:
    """profiles.extract stand-in: counts how many documents are read at once."""
    def __init__(self, delay=0.05):
        self.now = self.peak = self.calls = 0
        self.lock, self.delay = threading.Lock(), delay

    def __call__(self, path, profile, model=None, force_ocr=False):
        with self.lock:
            self.now += 1; self.calls += 1; self.peak = max(self.peak, self.now)
        time.sleep(self.delay)
        with self.lock:
            self.now -= 1
        return ({"profile": profile.id, "decision": "REVIEW", "reasons": ["r"], "fields": [], "tables": [], "checks": [], "text": []},
                {"reader_raw": {"big": "x"}, "file": path})


class LiveBatches(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (patch.object(batch_mod, "BATCH_DIR", Path(tmp.name)), patch.object(service, "_BATCHES", None)):
            p.start()
            self.addCleanup(p.stop)
        self.c = TestClient(service.app, raise_server_exceptions=False)

    def _wait(self, bid, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            st = self.c.get(f"/api/batches/{bid}").json()
            if st["finished"]:
                return st
            time.sleep(0.05)
        self.fail("batch did not finish")

    def test_profile_batch_runs_with_bounded_concurrency_and_valid_state(self):
        reader = _Reader()
        files = [("files", (f"doc{i}.pdf", _pdf() + bytes([i]), "application/pdf")) for i in range(12)]
        with patch.object(profiles, "extract", reader), patch.object(service, "_EXTRACT_SEM", threading.BoundedSemaphore(3)):
            r = self.c.post("/api/batches", files=files, data={"profile": "general"})
            self.assertEqual(r.status_code, 200, r.text)
            bid = r.json()["batch_id"]
            st = self._wait(bid)
        self.assertEqual(st["counts"], {"done": 12})
        self.assertLessEqual(reader.peak, 3)                       # never more documents at once than slots
        self.assertEqual({i["profile"] for i in st["items"]}, {"general"})
        res = self.c.get(f"/api/batches/{bid}/items/{st['items'][0]['id']}").json()
        self.assertEqual(res["result"]["decision"], "REVIEW")
        self.assertNotIn("reader_raw", res["audit"])
        saved = json.loads((batch_mod.BATCH_DIR / bid / "items.json").read_text())   # what a restart reads back
        self.assertEqual(len(saved["items"]), 12)
        self.assertFalse(list((batch_mod.BATCH_DIR / bid).glob(".*.tmp")))

    def test_auto_lets_the_router_pick_per_document(self):
        seen = []

        def fake(path, profile, model=None, force_ocr=False):
            seen.append(profile)
            return ({"profile": "gate_pass", "decision": "REVIEW", "reasons": [], "fields": [], "tables": [], "checks": [], "text": [],
                     "route": {"profile": "gate_pass"}}, {})
        with patch.object(profiles, "extract", fake):
            bid = self.c.post("/api/batches", files=[("files", ("a.pdf", _pdf(), "application/pdf"))], data={"profile": "auto"}).json()["batch_id"]
            st = self._wait(bid)
        self.assertEqual(seen, ["auto"])
        self.assertEqual((st["counts"], st["items"][0]["summary"]["profile"]), ({"done": 1}, "gate_pass"))

    def test_unknown_profile_is_refused_before_any_file_is_kept(self):
        r = self.c.post("/api/batches", files=[("files", ("a.pdf", _pdf(), "application/pdf"))], data={"profile": "nope"})
        self.assertEqual(r.status_code, 404)

    def test_same_file_is_read_again_after_the_profile_changes(self):
        reader = _Reader(0)
        files = [("files", ("a.pdf", _pdf(), "application/pdf"))]
        with patch.object(profiles, "extract", reader):
            self._wait(self.c.post("/api/batches", files=files, data={"profile": "general"}).json()["batch_id"])
            self._wait(self.c.post("/api/batches", files=files, data={"profile": "general"}).json()["batch_id"])
            self.assertEqual(reader.calls, 1)                      # identical file, same profile: reused
            with patch.object(profiles, "revision", lambda pid: "changed"):
                self._wait(self.c.post("/api/batches", files=files, data={"profile": "general"}).json()["batch_id"])
            self.assertEqual(reader.calls, 2)                      # a model switch reads it again

    def test_results_kept_in_memory_are_bounded(self):
        mgr = batch_mod.BatchManager(lambda *a: {"result": {"decision": "REVIEW"}}, workers=2)
        b = mgr.create()
        with patch.object(batch_mod, "RESULTS_IN_MEMORY", 5):
            for i in range(20):
                mgr._done(b.id, batch_mod.Item(f"i{i}", "f", 1, f"h{i}", "p"), {"n": i})
            self.assertEqual(len(mgr.results), 5)
            self.assertEqual(mgr.result(b.id, "i0"), {"n": 0})      # an evicted result comes back from disk

    def test_concurrent_workers_never_tear_the_batch_file(self):
        mgr = batch_mod.BatchManager(lambda *a: {}, workers=1)
        b = mgr.create()
        for i in range(40):
            b.items.append(batch_mod.Item(f"i{i}", "f" * 200, 1, "h", "p"))
        errors = []

        def hammer():
            for _ in range(30):
                mgr._persist(b.id)
                try:
                    json.loads((batch_mod.BATCH_DIR / b.id / "items.json").read_text())
                except ValueError as e:
                    errors.append(e)
        ts = [threading.Thread(target=hammer) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
