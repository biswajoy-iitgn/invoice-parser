"""v2 pipeline contracts: layout serialization, KV linking, citations, assembly and routing.

Offline and deterministic: the reader output is supplied directly and the semantic engine runs
with its model layers disabled (alias bank + lexicon + reader vote).
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SEMANTIC_DISABLE_MODELS"] = "1"

from invoice_extractor import semantic  # noqa: E402
from invoice_extractor.kv import candidates  # noqa: E402
from invoice_extractor.layout import Unit, serialize  # noqa: E402
from invoice_extractor.pipeline import PageData, assemble, iso6346_ok, locate, mawb_ok  # noqa: E402
from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402


def U(text, x0, y0, w=None, h=10, angle=0.0):
    w = w if w is not None else 6 * len(text)
    return Unit(text, (x0, y0, x0 + w, y0 + h), 1, 1.0, angle)


def page(lines):
    """lines: [(y, [(x, text), ...])] -> units with word boxes."""
    out = []
    for y, segs in lines:
        for x, text in segs:
            for k, word in enumerate(text.split()):
                out.append(U(word, x + sum(6 * len(w) + 6 for w in text.split()[:k]), y))
    return out


INVOICE = page([
    (10, [(10, "BLUE OCEAN LOGISTICS PVT LTD")]),
    (30, [(10, "Inv. No.: BOL/26-27/0042"), (300, "Dated: 14-Aug-2026")]),
    (50, [(10, "OCEAN BILL OF LADING"), (200, "HOUSE BILL OF LADING")]),
    (62, [(10, "MEDU1234567"), (200, "AID0019575")]),
    (80, [(10, "Container No: MSCU6639295")]),
    (100, [(10, "OTHER CHARGES"), (400, "28,025.00")]),
    (112, [(10, "THC")]),
    (130, [(10, "Warehouse Charge CFS"), (400, "14,320.00")]),
    (150, [(10, "Warehouse Charge CFS"), (400, "1,000.00")]),
    (170, [(10, "Sub Total"), (400, "43,345.00")]),
])


class LayoutTests(unittest.TestCase):
    def test_serialize_numbers_and_aligns(self):
        text, idx = serialize([(1, INVOICE)])
        self.assertTrue(text.startswith("=== PAGE 1 ==="))
        self.assertIn("[L1] BLUE OCEAN LOGISTICS PVT LTD", text)
        lines = text.splitlines()
        lab = next(l for l in lines if "HOUSE BILL" in l)
        val = next(l for l in lines if "AID0019575" in l)
        # label row and value row stay column-aligned
        self.assertEqual(lab.index("HOUSE") - lab.index("]"), val.index("AID0019575") - val.index("]"))
        self.assertEqual(len(idx), 10)

    def test_deskew_keeps_rows_together(self):
        import math

        a = math.radians(2.5)
        rot = []
        for u in INVOICE:
            cx, cy = (u.box[0] + u.box[2]) / 2, (u.box[1] + u.box[3]) / 2
            nx, ny = cx * math.cos(a) - cy * math.sin(a), cx * math.sin(a) + cy * math.cos(a)
            w = u.box[2] - u.box[0]
            rot.append(Unit(u.text, (nx - w / 2, ny - 5, nx + w / 2, ny + 5), 1, 1.0, 2.5))
        _, idx = serialize([(1, rot)])
        self.assertEqual(len(idx), 10, "skewed rows must still group into 10 lines")


class KVTests(unittest.TestCase):
    def test_inline_and_stacked_pairs(self):
        _, idx = serialize([(1, INVOICE)])
        kvs = {(k.label, k.value, k.layout) for k in candidates(idx)}
        self.assertIn(("Inv. No.", "BOL/26-27/0042", "inline"), kvs)
        self.assertIn(("HOUSE BILL OF LADING", "AID0019575", "stacked"), kvs)
        self.assertIn(("OCEAN BILL OF LADING", "MEDU1234567", "stacked"), kvs)


class CitationTests(unittest.TestCase):
    def setUp(self):
        _, self.idx = serialize([(1, INVOICE)])

    def test_exact_and_numeric(self):
        hits = locate("AID0019575", list(self.idx.values()))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["match"], "exact")
        self.assertGreater(hits[0]["bbox"][0], 150)  # the house B/L column, not the ocean one
        hits = locate(28025.0, list(self.idx.values()), numeric=True)
        self.assertEqual(hits[0]["text"], "28,025.00")

    def test_ocr_noise_is_fuzzy_not_exact(self):
        hits = locate("AID0O19575", list(self.idx.values()))   # OCR read 0 as O
        self.assertTrue(hits and hits[0]["match"] == "fuzzy")

    def test_absent_value_has_no_box(self):
        self.assertEqual(locate("XYZ9999999", list(self.idx.values())), [])

    def test_check_digits(self):
        self.assertTrue(iso6346_ok("MSCU6639295"))
        self.assertFalse(iso6346_ok("MSCU6639291"))
        self.assertTrue(mawb_ok("176-12345675"))
        self.assertFalse(mawb_ok("176-12345670"))


class AssembleTests(unittest.TestCase):
    def setUp(self):
        semantic._ENGINE = semantic.SemanticEngine(encoder_dir="/nonexistent", laya_dir="/nonexistent")
        self.text, self.idx = serialize([(1, INVOICE)])
        self.pages = [PageData(1, INVOICE, "text-layer", 600, 800)]
        L = {ln.text.strip(): lid for lid, ln in self.idx.items()}
        self.lid = lambda s: next(v for k, v in L.items() if s in k)
        self.raw = {
            "invoice_no": "BOL/26-27/0042", "invoice_date": "14-Aug-2026", "vendor_name": "BLUE OCEAN LOGISTICS PVT LTD",
            "transport_mode": "sea", "destination_country": "Germany", "container_numbers": ["MSCU6639295"],
            "mawb_no": "", "hawb_no": "", "master_bl_no": "MEDU1234567", "house_bl_no": "AID0019575",
            "gross_weight": "", "chargeable_weight": "", "exchange_rates": [],
            "line_items": [
                {"description": "OTHER CHARGES THC", "amount_inr": 28025.0, "category": "Terminal Handling Charges",
                 "lines": [self.lid("OTHER CHARGES"), self.lid("THC")]},
                {"description": "Warehouse Charge CFS", "amount_inr": 14320.0, "category": "Warehouse charges Port",
                 "lines": [self.lid("14,320.00")]},
                {"description": "Warehouse Charge CFS", "amount_inr": 1000.0, "category": "Warehouse charges Port",
                 "lines": [self.lid("1,000.00")]},
            ],
            "subtotal_before_tax": 43345.0, "total_invoice_amount": None,
            "evidence": {"invoice_no": [self.lid("Inv. No.")], "house_bl_no": [self.lid("AID0019575")]},
        }

    def test_fields_citations_and_sums(self):
        fields, audit = assemble(self.raw, self.text, self.idx, self.pages)
        self.assertEqual(list(fields), OUTPUT_FIELDS)
        self.assertEqual(fields["Invoice No"], "BOL/26-27/0042")
        self.assertEqual(fields["Invoice Date"], "2026-08-14")
        self.assertEqual(fields["Bill of Lading / HBL"], "AID0019575")
        self.assertEqual(fields["Container Number"], "MSCU6639295")
        self.assertEqual(fields["Warehouse charges Port"], "15320.00")       # sum of two cited lines
        self.assertEqual(fields["Terminal Handling Charges"], "28025.00")
        wh = audit["fields_explained"]["Warehouse charges Port"]
        self.assertEqual(wh["basis"], "sum-of-cited-lines")
        self.assertEqual(len(wh["contributions"]), 2)
        for f, v in fields.items():
            if v:
                self.assertTrue(audit["citations"].get(f), f"{f} has no citation box")
        self.assertTrue(audit["subtotal_reconciled"])
        links = audit["fields_explained"]["Bill of Lading / HBL"].get("label_links", [])
        self.assertTrue(any(l["label"] == "HOUSE BILL OF LADING" for l in links))

    def test_unprinted_values_are_blanked_and_routed_to_review(self):
        raw = dict(self.raw, invoice_no="INV-NOT-PRINTED", line_items=self.raw["line_items"] + [
            {"description": "Sub Total", "amount_inr": 43345.0, "category": "Other Charges", "lines": [self.lid("Sub Total")]}])
        fields, audit = assemble(raw, self.text, self.idx, self.pages)
        self.assertEqual(fields["Invoice No"], "")
        self.assertEqual(audit["review_route"], "REVIEW")
        self.assertTrue(any("not printed" in w for w in audit["warnings"]))
        self.assertEqual(fields["Other Charges"], "", "a Sub Total row must never become a charge")

    def test_clean_invoice_can_auto_accept(self):
        os.environ["AUTO_ACCEPT"] = "1"
        _, audit = assemble(self.raw, self.text, self.idx, self.pages)
        if not audit["warnings"]:
            self.assertEqual(audit["review_route"], "AUTO_ACCEPT")


if __name__ == "__main__":
    unittest.main()


class ScanRobustness(unittest.TestCase):
    """OCR artefacts on scanned tables, and the editable category rules."""

    def _lines(self, rows):
        from invoice_extractor.layout import Line, Unit

        out = []
        for n, (y, segs) in enumerate(rows, 1):
            units = [Unit(t, (x, y, x + 6.0 * len(t), y + 10.0), 1) for x, t in segs]
            out.append(Line(f"L{n}", 1, "  ".join(u.text for u in units),
                            (units[0].box[0], y, units[-1].box[2], y + 10.0), units))
        return out

    def test_amount_recovered_from_merged_ocr_token(self):
        from invoice_extractor.pipeline import locate

        lines = self._lines([(100, [(10, "7 DAP CHARGES"), (300, "635.00114.2600 T"), (420, "72,555.109.00 6,529.96")])])
        hit = locate(72555.10, lines, numeric=True)
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["match"], "split-merged-number")
        self.assertEqual(hit[0]["text"], "72,555.10")
        x0, _, x1, _ = hit[0]["bbox"]
        self.assertAlmostEqual(x0, 420.0, places=1)                 # starts where the token starts
        self.assertLess(x1, 420 + 6.0 * len("72,555.109.00"))       # and stops before the glued "9.00"
        self.assertEqual(locate(2555.10, lines, numeric=True), [])  # never cut out of the middle of a number
        self.assertEqual(locate(635.00, lines, numeric=True)[0]["text"], "635.00")

    def test_indian_grouping_and_box_inside_segment(self):
        from invoice_extractor.pipeline import _num_forms, locate

        self.assertIn("5,30,230.50", _num_forms(530230.5))
        lines = self._lines([(50, [(10, "Invoice No: AB-1234  Date 01-02-2026")])])
        hit = locate("AB-1234", lines)[0]
        self.assertGreater(hit["bbox"][0], 10 + 6.0 * 10)           # box starts at the value, not at the label
        self.assertLess(hit["bbox"][2], 10 + 6.0 * 22)

    def test_business_rules(self):
        from invoice_extractor.semantic import business_rule

        self.assertEqual(business_rule("Haulage (delivery)")["field"], "DAP Charges")
        self.assertIsNone(business_rule("Inland Haulage Charges (IHC)"))
        self.assertIsNone(business_rule("OTHER CHARGES HAULAGE FROM PORT TO WAREHOUSE"))
        self.assertIsNone(business_rule("Export haulage to door"))
        r = business_rule("ENS / ACD / AMS / ISF Charges")
        self.assertEqual((r["field"], r["review"]), ("AMS CHARGES", True))
        self.assertIsNone(business_rule("AMS Security Fee Export"))
        self.assertIsNone(business_rule("ORIGIN CHARGES ENS / ICS2 (18%)"))     # one (EU) filing, two names

    def test_weight_needs_a_weight_label_or_unit(self):
        from invoice_extractor.pipeline import _weight_like

        self.assertTrue(_weight_like("WGHT", "6750.500"))
        self.assertTrue(_weight_like("GROSS WT.", "2784.000 KGS"))
        self.assertTrue(_weight_like("Cargo details", "1,200 kg"))
        self.assertFalse(_weight_like("W/M", "996521"))
