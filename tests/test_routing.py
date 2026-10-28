"""The router (which profile, which models) and the model pool's budget, without loading any model."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MODEL_POOL", "off")

from invoice_extractor import model_pool, profiles, router  # noqa: E402
from invoice_extractor.layout import Line  # noqa: E402


def index(*lines):
    return {f"L{i}": Line(f"L{i}", 1, t, (0, i * 10, 500, i * 10 + 8), []) for i, t in enumerate(lines, 1)}


class Detect(unittest.TestCase):
    def setUp(self):
        self.ps = profiles.load_all()

    def test_documents_go_to_their_profile_and_doubt_goes_to_general(self):
        inv = "TAX INVOICE  Invoice No 7044  Consignee ACME  Port of Loading Hamburg  Ocean Freight 1,000.00  Bill of Lading HLCU1"
        self.assertEqual(router.detect(inv, self.ps)[0], "logistics_invoice")
        self.assertEqual(router.detect("GATE PASS  Returnable  Vehicle No GJ01AB1234  Driver Ramesh", self.ps)[0], "gate_pass")
        self.assertEqual(router.detect("Dear Sir, we refer to your letter of 3 March.", self.ps)[0], "general")
        pid, _, why = router.detect("Weekly roster", self.ps)          # one weak hint is not enough
        self.assertEqual(pid, "general")
        self.assertIn("below", why)

    def test_the_table_reader_loads_only_for_a_table(self):
        p = self.ps["logistics_invoice"]
        prose = index("TAX INVOICE No 1", "Thank you for your business")
        table = index("Freight 1 1,000.00 1,000.00", "THC 1 250.00 250.00", "Seal 1 50.00 50.00")
        with patch.object(router, "reader_id", lambda r=None: "qwen3-vl-4b"):
            self.assertEqual(router.plan(p, "", table, True).tables, "reader")                     # field profiles: the reader's own tables
            self.assertEqual(router.plan(self.ps["general"], "", table, False).tables, "document-parser")
            self.assertEqual(router.plan(self.ps["general"], "", prose, False).tables, "reader")   # no table: the parser stays unloaded

    def test_a_reader_not_on_this_machine_falls_back_to_the_default(self):
        with patch.object(model_pool.ModelPool, "available", lambda self, mid: mid == "qwen3-vl-4b"):
            self.assertEqual(router.reader_id("qwen3-vl-30b-a3b"), "qwen3-vl-4b")
            self.assertEqual(router.reader_id("invoice-lora"), "qwen3-vl-4b")


class NuExtractTemplate(unittest.TestCase):
    def test_a_profile_schema_becomes_a_nuextract_template(self):
        from invoice_extractor import pipeline

        t = pipeline.nuextract_template(profiles.json_schema(profiles.get("gate_pass")))
        self.assertEqual(t["vehicle_no"], "verbatim-string")
        self.assertEqual(t["pass_type"], ["RETURNABLE", "NON-RETURNABLE"])             # a choice field is an enum
        self.assertEqual(t["items"], [{"Description": "verbatim-string", "Qty": "verbatim-string", "Unit": "verbatim-string"}])
        self.assertNotIn("evidence", t)


class _Proc:
    def __init__(self):
        self.alive = True
        self.pid = -1

    def poll(self):
        return None if self.alive else 0


class Pool(unittest.TestCase):
    def setUp(self):
        self.pool = model_pool.ModelPool(budget_gb=14, idle_seconds=3600)
        for mid, gb in (("reader", 10), ("tables", 3), ("second", 3), ("huge", 20)):
            self.pool.register(mid, f"/models/{mid}", gb)
        for name, fn in (("_start", lambda s: setattr(s, "proc", _Proc())), ("_stop", lambda s: setattr(s, "proc", None))):
            p = patch.object(self.pool, name, fn)
            p.start()
            self.addCleanup(p.stop)
        for p in (patch.object(model_pool, "_on_disk", lambda path: True), patch.object(model_pool, "_wait_ready", lambda s, d: None),
                  patch.dict(os.environ, {"MODEL_POOL": "on"})):
            p.start()
            self.addCleanup(p.stop)

    def test_least_recently_used_idle_model_makes_room(self):
        with self.pool.use("reader"):
            pass
        time.sleep(0.01)
        with self.pool.use("tables"):
            pass
        with self.pool.use("second"):                                 # 10 + 3 + 3 > 14: the reader, least recently used, goes
            self.assertEqual(sorted(m["model"] for m in self.pool.loaded()), ["second", "tables"])

    def test_a_busy_model_is_never_stopped_and_a_too_big_one_is_refused(self):
        with self.pool.use("reader"), self.pool.use("tables"):
            done = []
            t = threading.Thread(target=lambda: done.append(self._use("second")))
            t.start()
            t.join(0.3)
            self.assertEqual(done, [])                                 # waits: everything loaded is busy
            self.assertEqual(sorted(m["model"] for m in self.pool.loaded()), ["reader", "tables"])
        t.join(5)
        self.assertEqual(done, ["second"])                             # ran once a model was free
        with self.assertRaisesRegex(RuntimeError, "more than MODEL_MEMORY_GB"):
            self._use("huge")

    def test_pool_off_starts_nothing(self):
        with patch.dict(os.environ, {"MODEL_POOL": "off"}), self.assertRaisesRegex(RuntimeError, "pool is off"):
            self._use("reader")

    def _use(self, mid):
        with self.pool.use(mid):
            return mid


if __name__ == "__main__":
    unittest.main()
