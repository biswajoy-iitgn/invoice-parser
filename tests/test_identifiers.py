"""Identifier validators: shape and check digits, with real identifiers from the sample documents as the oracle."""
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.identifiers import parse_date, validate  # noqa: E402


class IdentifierTests(unittest.TestCase):
    def test_gstin_check_character(self):
        self.assertEqual("", validate("gstin", "24AACCH5549Q1Z2"))       # real GSTINs printed on the sample invoices
        self.assertEqual("", validate("gstin", "24AAACK2676H1ZM"))
        self.assertEqual("", validate("gstin", "24 AAACK2676H1ZM"))
        self.assertIn("should be M, printed N", validate("gstin", "24AAACK2676H1ZN"))
        self.assertIn("should be 2", validate("gstin", "24AACCH5549Q1Z3"))
        self.assertIn("not shaped like a GSTIN", validate("gstin", "24AAACK2676H1"))
        self.assertIn("state code 77", validate("gstin", "77AAACK2676H1ZM"))

    def test_shapes(self):
        ok = [("pan", "AACCH5549Q"), ("ifsc", "HDFC0001234"), ("mobile", "9484452587"), ("mobile", "+91 94844 52587"), ("pin", "380054"),
              ("email", "a.b@harsha.co.in"), ("vehicle", "GJ-33-SN-5969"), ("vehicle", "GJ32T 3525"), ("vehicle", "GJ12CT9699"), ("vehicle", "22BH1234AA"),
              ("irn", "b79f2bd27b2d95f758cb54ca48c048bdf6896d65efb7a5c3155331ee8888f860"), ("hsn", "996719"), ("hsn", "7208")]
        for kind, v in ok:
            self.assertEqual("", validate(kind, v), (kind, v))
        bad = [("pan", "AACCH5549"), ("pan", "AACXH5549Q"), ("ifsc", "HDFC1001234"), ("mobile", "5484452587"), ("mobile", "948445258"), ("pin", "038005"),
               ("email", "a@b"), ("vehicle", "ZZ-33-SN-5969"), ("vehicle", "GJ33"), ("irn", "b79f"), ("hsn", "99671")]
        for kind, v in bad:
            self.assertNotEqual("", validate(kind, v), (kind, v))

    def test_container_and_air_waybill_reuse_the_pipelines_checks(self):
        self.assertEqual("", validate("container", "CSQU3054383"))          # ISO 6346 worked example
        self.assertIn("check digit", validate("container", "CSQU3054384"))
        self.assertEqual("", validate("mawb", "176-1234567" + "5"))         # 1234567 mod 7 = 5
        self.assertIn("mod 7", validate("mawb", "176-1234567" + "4"))
        self.assertIn("11 digits", validate("mawb", "17612"))

    def test_dates(self):
        d = parse_date
        self.assertEqual(date(2026, 3, 31), d("31.03.2026"))
        self.assertEqual(date(2026, 4, 7), d("7-4-26"))
        self.assertEqual(date(2026, 3, 31), d("2026-03-31"))
        self.assertEqual(date(2026, 4, 6), d("06 Apr 2026"))
        self.assertIsNone(d("01.05.202€"))
        self.assertIsNone(d("32.01.2026"))
        self.assertIsNone(d("31-02-2026"))
        self.assertEqual("", validate("date", "31.03.2026"))
        self.assertIn("not a recognised", validate("date", "01.05.202€"))
        self.assertIn("not a plausible", validate("date", "01.05.2099"))
        self.assertIn("not a plausible", validate("date", "01.05.1985"))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            validate("passport", "x")


if __name__ == "__main__":
    unittest.main()
