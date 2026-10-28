import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.generate_dataset import FIELDS, render  # noqa: E402


class GeneratorContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="inv-gen-test-"))
        cls.recs = [render(i, 91731 + i, i % 5, (i % 5) * 10 + (i % 10),
                           None, cls.tmp, False) for i in range(30)]

    def test_all_gt_keys_always_present(self):
        for r in self.recs:
            self.assertEqual(set(r["ground_truth"]), set(FIELDS), r["id"])

    def test_distinct_field_per_charge_row(self):
        # Each GT charge value must come from exactly one printed row, so no
        # last-wins overwrite can make GT disagree with the rendered amounts.
        for r in self.recs:
            fields = [b["field"] for b in r["regions"] if b["field"] not in
                      ("DECOY", "ANNOTATION", "Invoice No", "Invoice Date",
                       "Container Number", "MAWB No", "HAWB No", "Vendor Name",
                       "Bill of Lading / HBL", "WEIGHT", "Ex Rate")]
            self.assertEqual(len(fields), len(set(fields)),
                             f"{r['id']}: {fields}")

    def test_ex_rate_format_matches_pipeline(self):
        for r in self.recs:
            v = r["ground_truth"]["Ex Rate"]
            if v:
                self.assertRegex(v, r"^USD \d+(\.\d+)?$", r["id"])

    def test_decoy_never_leaks_into_gt(self):
        for r in self.recs:
            texts = " ".join(b["text"] for b in r["regions"]
                             if b["field"] == "DECOY")
            for m in re.findall(r"SL\d+", texts):
                for v in r["ground_truth"].values():
                    self.assertNotIn(m, str(v), r["id"])

    def test_annotations_never_leak_into_gt(self):
        for r in self.recs:
            for b in r["regions"]:
                if b["field"] != "ANNOTATION":
                    continue
                for v in r["ground_truth"].values():
                    self.assertNotIn(b["text"], str(v), r["id"])

    def test_hbl_variants_stay_canonical(self):
        for r in self.recs:
            hbl_regions = [b for b in r["regions"]
                           if b["field"] == "Bill of Lading / HBL"]
            self.assertEqual(len(hbl_regions), 1, r["id"])
            self.assertIn(r["ground_truth"]["Bill of Lading / HBL"],
                          hbl_regions[0]["text"])


if __name__ == "__main__":
    unittest.main()
