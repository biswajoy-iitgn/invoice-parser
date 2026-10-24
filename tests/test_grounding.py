"""Citation-box grounding contracts (tesseract-backed, dataset-anchored)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.grounding import cite_fields, cite_image_value  # noqa: E402

IMG = str(ROOT / "dataset" / "images" / "INV_SYN_003001.png")


def _inside(hit_bbox, gt):
    x1, y1 = max(hit_bbox[0], gt[0]), max(hit_bbox[1], gt[1])
    x2, y2 = min(hit_bbox[2], gt[2]), min(hit_bbox[3], gt[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = (hit_bbox[2] - hit_bbox[0]) * (hit_bbox[3] - hit_bbox[1])
    return inter / area > 0.5 if area else False


class GroundingContracts(unittest.TestCase):
    def test_header_value_grounded_in_row(self):
        hits = cite_image_value(IMG, "INV-2024-03001")
        self.assertTrue(hits)
        self.assertTrue(_inside(hits[0].bbox, [82, 275, 1193, 299]))

    def test_amount_citation_covers_label_row(self):
        hits = cite_image_value(IMG, "7778.72")
        self.assertTrue(hits)
        self.assertIn("THC", hits[0].source_text)

    def test_missing_value_has_no_box(self):
        self.assertEqual(cite_image_value(IMG, "ZZZ-NOT-PRINTED-999"), [])
        self.assertEqual(cite_image_value(IMG, ""), [])

    def test_cite_fields_batch(self):
        out = cite_fields({"Invoice No": "INV-2024-03001", "Misc": ""}, IMG)
        self.assertTrue(out["Invoice No"])
        self.assertEqual(out["Misc"], [])


if __name__ == "__main__":
    unittest.main()
