"""Unit tests for paddle_backends normalization (no paddle import needed)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.paddle_backends import (  # noqa: E402
    _blocks_of,
    _ordered_ocr_text,
)


def _s3_page():
    # Overlapping legs: region boxes + table cells + overall OCR repeat text.
    return {
        "layout_det_res": {"boxes": [
            {"label": "text", "bbox": [0, 0, 10, 10], "text": "", "score": 0.9}]},
        "overall_ocr_res": {
            "rec_texts": ["TOTAL", "TAX INVOICE", "Footer note", "TOTAL"],
            "rec_boxes": [[5, 90, 50, 100], [5, 5, 100, 20],
                          [5, 200, 90, 210], [60, 90, 105, 100]],
            "rec_scores": [0.99, 0.99, 0.98, 0.99]},
        "table_res_list": [{"cell_box_list": [[[5, 90], [50, 100]]],
                            "table_ocr_pred": {"rec_texts": ["TOTAL"]}}],
    }


class OrderedOcrTest(unittest.TestCase):
    def test_reading_order_and_adjacent_dedupe(self):
        text = _ordered_ocr_text(_s3_page())
        # Sorted top-to-bottom; adjacent duplicate TOTAL collapsed once.
        self.assertEqual(text, "TAX INVOICE\nTOTAL\nFooter note")

    def test_empty_without_ocr_leg(self):
        self.assertEqual(_ordered_ocr_text({}), "")
        self.assertEqual(_ordered_ocr_text({"overall_ocr_res": {}}), "")

    def test_blocks_keep_all_legs_for_evidence(self):
        blocks = _blocks_of(_s3_page())
        labels = [b["label"] for b in blocks]
        self.assertIn("text", labels)
        self.assertIn("ocr_text", labels)
        self.assertTrue(any(l.startswith("table_") for l in labels))

    def test_vl_block_objects(self):
        class Block:
            label = "header"
            bbox = [66, 54, 421, 108]
            content = "DSV Air & Sea"
            score = 0.9
        blocks = _blocks_of({"parsing_res_list": [Block()]})
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["text"], "DSV Air & Sea")
        self.assertEqual(blocks[0]["bbox"], [66, 54, 421, 108])


if __name__ == "__main__":
    unittest.main()
