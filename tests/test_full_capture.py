"""Full capture (facts.py), evidence images, the reader-first category policy and the vendor identity key."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.layout import Unit, serialize  # noqa: E402

CW = 5.0     # width of one character in the synthetic layout below


def page(rows: list[list[tuple[int, str]]]) -> dict:
    """rows: [[(column in characters, text), ...], ...] -> the layout index of a one-page document."""
    units = []
    for r, cells in enumerate(rows):
        for col, text in cells:
            x = col * CW
            for word in text.split(" "):
                units.append(Unit(word, (x, 20.0 * r, x + CW * len(word), 20.0 * r + 10.0), 1))
                x += CW * (len(word) + 1)
    return serialize([(1, units)])[1]


class Facts(unittest.TestCase):
    def facts(self, rows):
        from invoice_extractor.facts import document_facts

        return document_facts(page(rows))

    def pairs(self, rows):
        return {f["label"]: f["value"] for f in self.facts(rows) if f["kind"] in ("pair", "block")}

    def test_inline_pairs_and_types(self):
        fs = self.facts([[(0, "GSTIN : 27VKECD7915X9Z7"), (40, "State : [27] MAHARASHTRA")],
                         [(0, "Tel: +91-70-54211460 Email: billing@keystone.net")],
                         [(0, "Bank Details: Kotak Mahindra Bank A/c No: 687199221484 IFSC: HDFC0007987")]])
        got = {f["label"]: (f["value"], f["value_type"]) for f in fs if f["kind"] == "pair"}
        self.assertEqual(got["GSTIN"], ("27VKECD7915X9Z7", "gstin"))
        self.assertEqual(got["Email"], ("billing@keystone.net", "email"))
        self.assertEqual(got["Tel"][0], "+91-70-54211460")
        self.assertEqual(got["Bank Details"][0], "Kotak Mahindra Bank")
        self.assertEqual(got["A/c No"], ("687199221484", "number"))
        self.assertEqual(got["IFSC"], ("HDFC0007987", "ifsc"))

    def test_label_value_grid(self):
        got = self.pairs([[(0, "INCOTERMS"), (16, "DAP"), (60, "P. OF DISCHARGE"), (80, "FRLEH")],
                          [(0, "NO. OF PKGS"), (16, "72 BOXES"), (60, "OUR B/L NO"), (80, "NVMEXP3484")],
                          [(0, "N.W."), (16, "17899.791 KGS."), (60, "EQUIPMENT NO"), (80, "GESU6201748")],
                          [(0, "VESSEL"), (16, "CMA CGM SAGAR"), (60, "WT."), (80, "19349.971 KGS.")]])
        self.assertEqual(got["INCOTERMS"], "DAP")                 # not "INCOTERMS -> NO. OF PKGS"
        self.assertEqual(got["OUR B/L NO"], "NVMEXP3484")
        self.assertEqual(got["N.W."], "17899.791 KGS.")
        self.assertEqual(got["VESSEL"], "CMA CGM SAGAR")

    def test_table_cells_get_their_column_title(self):
        fs = self.facts([[(0, "DESCRIPTION"), (60, "Charge + Tax IN INR"), (90, "TAX IN INR"), (120, "AMOUNT IN INR")],
                         [(0, "Seal Fee FCL P.O.L"), (81, "1,382.58 CGST 9%=105.45,"), (126, "1,171.68")],
                         [(90, "SGST 9%=105.45")],
                         [(0, "Trucking"), (80, "33,759.80 CGST 9%=2,574.90,"), (125, "28,610.00")],
                         [(110, "Net Total 1,88,151.93")]])
        cells = [(f["label"], f["value"]) for f in fs if f["kind"] == "table" and f["label"] != "(column title)"]
        self.assertIn(("DESCRIPTION", "Seal Fee FCL P.O.L"), cells)
        self.assertIn(("Charge + Tax IN INR", "1,382.58"), cells)
        self.assertIn(("TAX IN INR", "CGST 9%=105.45,"), cells)       # one printed run, cut at the column
        self.assertIn(("TAX IN INR", "SGST 9%=105.45"), cells)        # the row's second line
        self.assertIn(("AMOUNT IN INR", "28,610.00"), cells)
        total = [f for f in fs if f["label"] == "Net Total"]
        self.assertEqual((total[0]["kind"], total[0]["value"], total[0]["value_type"]), ("pair", "1,88,151.93", "amount"))

    def test_stacked_headers_and_single_run_pairs(self):
        got = self.pairs([[(0, "CONSIGNOR"), (60, "CONSIGNEE")],
                          [(0, "HARSHA ENGINEERS INTERNATIONAL LIMITED"), (60, "EPT GLOBAL LOGISTICS USA LLC")],
                          [(0, "DUE DATE 15-Oct-26"), (60, "TAX INVOICE IN1AMD261031252")],
                          [(0, "BL No"), (16, ": 5836209")]])
        self.assertEqual(got["CONSIGNOR"], "HARSHA ENGINEERS INTERNATIONAL LIMITED")
        self.assertEqual(got["CONSIGNEE"], "EPT GLOBAL LOGISTICS USA LLC")
        self.assertEqual(got["DUE DATE"], "15-Oct-26")
        self.assertEqual(got["TAX INVOICE"], "IN1AMD261031252")
        self.assertEqual(got["BL No"], "5836209")

    def test_nothing_printed_is_lost(self):
        rows = [[(0, "KEYSTONE EXPRESS CARGO LTD."), (70, "SERVICE INVOICE")],
                [(0, "448, Block 9, Orchid Tower")],
                [(0, "This is an electronically generated invoice, no signature is required.")]]
        fs = self.facts(rows)
        printed = " ".join(t for r in rows for _, t in r).replace(" ", "")
        captured = " ".join(f["value"] for f in fs).replace(" ", "")
        self.assertEqual(sorted(printed), sorted(captured))
        self.assertTrue(all(len(f["bbox"]) == 4 and f["bbox"][2] > f["bbox"][0] for f in fs))

    def test_fact_is_linked_to_the_output_field_it_feeds(self):
        from invoice_extractor.facts import document_facts

        index = page([[(0, "INVOICE REF. NO."), (30, "KEC/NVM/47867")], [(0, "PO NO"), (30, "PO/25/4165")],
                      [(0, "DUE DATE"), (30, "Jul 20, 2025")]])
        box = next(u.box for l in index.values() for u in l.units if u.text == "KEC/NVM/47867")
        fs = document_facts(index, {"Invoice No": [{"page": 1, "bbox": list(box)}]})
        self.assertEqual([f["field"] for f in fs if f["value"] == "KEC/NVM/47867"], ["Invoice No"])
        self.assertEqual([f["field"] for f in fs if f["value"] == "PO/25/4165"], [""])


class EvidenceImage(unittest.TestCase):
    def test_boxes_legend_and_hash(self):
        from PIL import Image

        from invoice_extractor.evidence_image import marks, render_evidence

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "invoice.png"
            Image.new("RGB", (900, 1200), "white").save(src)
            fields = {"Invoice No": "INV-1", "Sea Freight": "500.00", "Vendor Name": ""}
            audit = {"review_route": "REVIEW",
                     "citations": {"Invoice No": [{"page": 1, "bbox": [600, 80, 700, 100]}],
                                   "Sea Freight": [{"page": 1, "bbox": [760, 500, 840, 520]}]},
                     "fields_explained": {"Invoice No": {"confidence": 1.0}},
                     "line_items": [{"description": "Ocean freight", "amount_inr": 500.0, "category": "Sea Freight",
                                     "category_confidence": 0.97}],
                     "line_item_citations": [[{"page": 1, "bbox": [760, 500, 840, 520]}]],
                     "facts": [{"page": 1, "bbox": [100, 300, 220, 318], "label": "PO NO", "value": "PO/1", "field": "",
                                "section": ""}]}
            self.assertEqual([(m["kind"], m["name"]) for m in marks(fields, audit)],
                             [("field", "Invoice No"), ("charge", "Sea Freight")])
            out = render_evidence(str(src), fields, audit, Path(tmp) / "ev", revision="test")
            self.assertEqual(len(out), 1)
            f = Path(tmp) / "ev" / out[0]["file"]
            self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(), out[0]["sha256"])
            self.assertEqual(out[0]["boxes"], 3)                       # two output values + one other item
            with Image.open(f) as im:
                self.assertGreater(im.width, 900)                      # the page plus the legend panel
                px = im.convert("RGB").getpixel((598, 90))             # left edge of the red box around the invoice number
                self.assertTrue(px[0] > 150 and px[1] < 110, px)


class CategoryPolicy(unittest.TestCase):
    W = {}

    def decide(self, votes, alias=None, learned=False, rule=None):
        from invoice_extractor.semantic import _reader_first

        return _reader_first(votes, alias, learned, rule, "", "", self.W)

    def test_reader_confirmed_by_a_model_is_accepted(self):
        d = self.decide({"reader": ("Sea Freight", 1.0), "embedding": ("Sea Freight", 0.9), "laya": ("Transportation", 1.0)})
        self.assertEqual((d.field, d.review), ("Sea Freight", ""))
        self.assertGreaterEqual(d.confidence, 0.9)

    def test_models_alone_do_not_overrule_the_reader_unless_sure(self):
        unsure = self.decide({"reader": ("Other Charges", 1.0), "embedding": ("Air Freight", 0.4), "laya": ("Air Freight", 1.0)})
        self.assertEqual(unsure.field, "Other Charges")
        self.assertLess(unsure.confidence, 0.9)                         # goes to review
        self.assertIn("Air Freight", unsure.review)
        sure = self.decide({"reader": ("Sea Freight", 1.0), "embedding": ("Pre Carriage Haulage", 0.96),
                            "laya": ("Pre Carriage Haulage", 1.0)})
        self.assertEqual(sure.field, "Pre Carriage Haulage")
        self.assertLess(sure.confidence, 0.9)
        self.assertTrue(sure.review)

    def test_alias_and_rule_come_first(self):
        votes = {"reader": ("Other Charges", 1.0), "embedding": ("Other Charges", 0.9), "laya": ("Other Charges", 1.0)}
        self.assertEqual(self.decide(votes, alias=("BL Charges", "ontology", "bl fee")).field, "BL Charges")
        self.assertEqual(self.decide(votes, rule={"field": "DAP Charges", "name": "x"}).field, "DAP Charges")
        self.assertEqual(self.decide(votes, alias=("VGM", "learned", "x"), learned=True,
                                     rule={"field": "DAP Charges", "name": "x"}).field, "VGM")

    def test_no_reader_category_falls_back_to_the_models(self):
        self.assertIsNone(self.decide({"embedding": ("Sea Freight", 0.9), "laya": ("Sea Freight", 1.0)}))


class HeaderRules(unittest.TestCase):
    def test_document_number_shape(self):
        from invoice_extractor.pipeline import COPY_MARK_RE, _doc_no_like

        self.assertTrue(_doc_no_like("MEDUAMD6709078"))
        self.assertTrue(_doc_no_like("157-65427040"))
        self.assertFalse(_doc_no_like("996759"))            # HSN / SAC code
        self.assertFalse(_doc_no_like("515.79"))            # an amount
        self.assertFalse(_doc_no_like("05-06-2025"))        # a date
        self.assertEqual(COPY_MARK_RE.sub("", "DEL/AX/2020 ORIGINAL"), "DEL/AX/2020")
        self.assertEqual(COPY_MARK_RE.sub("", "KEC/NVM/47867"), "KEC/NVM/47867")


class VendorIdentity(unittest.TestCase):
    def test_spelling_variants_are_one_vendor(self):
        from database.db import vendor_key

        self.assertEqual(vendor_key("JEENA & COMPANY"), vendor_key("Jeena and Company"))
        self.assertEqual(vendor_key("Trans Global Logistics Pvt. Ltd."), vendor_key("TRANS GLOBAL LOGISTICS PRIVATE LIMITED"))
        self.assertNotEqual(vendor_key("EPT Global Logistics Pvt Ltd"), vendor_key("EPT EXIM SERVICES PVT LTD"))


class OdbcIni(unittest.TestCase):
    def test_section_is_replaced_in_place(self):
        spec = importlib.util.spec_from_file_location("drv", ROOT / "odbc" / "build_excel_pg_driver.py")
        drv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(drv)
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "odbc.ini"
            ini.write_text("[OTHER]\nDriver = x\n; a comment that must survive\n\n[MINE]\nServername = old\n")
            link = Path(tmp) / "link.ini"
            os.link(ini, link)                                  # Excel sees the file through a hard link
            drv.write_ini(ini, "MINE", {"Servername": "new", "Port": "5432"}, "ODBC Data Sources", "PostgreSQL Free")
            text = link.read_text()                             # same file: the link sees the change
            self.assertIn("; a comment that must survive", text)
            self.assertIn("Servername = new", text)
            self.assertNotIn("Servername = old", text)
            self.assertIn("MINE = PostgreSQL Free", text)
            self.assertEqual(text.count("[MINE]"), 1)
            self.assertEqual(os.stat(ini).st_ino, os.stat(link).st_ino)


class Organizer(unittest.TestCase):
    """The language model only proposes; these rules decide what is stored (the model call is replaced here)."""

    FACTS = [
        {"seq": 1, "kind": "text", "label": "", "value": "KEYSTONE EXPRESS CARGO LTD.", "page": 1, "bbox": [10, 10, 200, 20], "line": "L1", "section": "", "field": "Vendor Name"},
        {"seq": 2, "kind": "pair", "label": "GSTIN", "value": "27VKECD7915X9Z7", "page": 1, "bbox": [10, 30, 100, 40], "line": "L2", "section": "", "field": ""},
        {"seq": 3, "kind": "pair", "label": "DUE DATE", "value": "Jul 20, 2025", "page": 1, "bbox": [300, 50, 360, 60], "line": "L3", "section": "", "field": ""},
        {"seq": 4, "kind": "pair", "label": "PAN", "value": "AACCD3848A, DSV Air & Sea Pvt. Ltd. - I721", "page": 1, "bbox": [10, 70, 300, 80], "line": "L4", "section": "", "field": ""},
        {"seq": 5, "kind": "text", "label": "", "value": "ENGINEERING ITEMS", "page": 1, "bbox": [10, 90, 100, 100], "line": "L5", "section": "", "field": ""},
        {"seq": 6, "kind": "text", "label": "", "value": "PRESSED TAPER ROLLER", "page": 1, "bbox": [110, 90, 230, 100], "line": "L5", "section": "", "field": ""},
        {"seq": 7, "kind": "text", "label": "", "value": "PO CHANGODAR", "page": 1, "bbox": [10, 110, 90, 120], "line": "L6", "section": "", "field": ""},
    ]

    def run_with(self, proposals):
        from unittest import mock

        from invoice_extractor import organizer

        with mock.patch.object(organizer, "_call", return_value=(proposals, {})):
            return organizer.organize(self.FACTS)

    def test_values_are_tied_to_extracted_items(self):
        r = self.run_with([
            {"detail": "vendor_gstin", "value": "27VKECD7915X9Z7", "line": "L2"},
            {"detail": "due_date", "value": "Jul 20, 2025", "line": "L9"},                  # wrong line: found elsewhere
            {"detail": "vendor_pan", "value": "AACCD3848A", "line": "L4"},                   # part of an item
            {"detail": "goods_description", "value": "ENGINEERING ITEMS PRESSED TAPER ROLLER", "line": "L5"},   # two items
            {"detail": "incoterm", "value": "FOB", "line": "L3"},                            # printed nowhere
            {"detail": "amount_in_words", "value": "Rupees Nine Thousand Only", "line": "L1"},
        ])
        got = {d["detail"]: d for d in r["details"]}
        self.assertEqual(got["vendor_gstin"]["items"], [2])
        self.assertEqual((got["due_date"]["items"], got["due_date"]["normalized"], got["due_date"]["valid"]), ([3], "2025-07-20", True))
        self.assertIn("elsewhere", got["due_date"]["grounding"])
        self.assertEqual((got["vendor_pan"]["value"], got["vendor_pan"]["items"], got["vendor_pan"]["valid"]), ("AACCD3848A", [4], True))
        self.assertEqual(got["goods_description"]["items"], [5, 6])
        self.assertEqual(got["goods_description"]["bbox"], [10.0, 90.0, 230.0, 100.0])       # the union of the two items
        self.assertNotIn("incoterm", got)
        self.assertNotIn("amount_in_words", got)
        self.assertEqual(sorted(d["detail"] for d in r["dropped"]), ["amount_in_words", "incoterm"])

    def test_type_checks_mark_wrong_assignments(self):
        r = self.run_with([
            {"detail": "customer_gstin", "value": "KEYSTONE EXPRESS CARGO LTD.", "line": "L1"},   # not a GSTIN
            {"detail": "po_number", "value": "PO CHANGODAR", "line": "L6"},                        # an address line, no digit
            {"detail": "vendor_gstin", "value": "27VKECD7915X9Z7", "line": "L2"},
            {"detail": "vendor_gstin", "value": "AACCD3848A", "line": "L4"},                       # a second value: ignored
        ])
        got = {d["detail"]: d for d in r["details"]}
        self.assertFalse(got["customer_gstin"]["valid"])
        self.assertFalse(got["po_number"]["valid"])
        self.assertTrue(got["vendor_gstin"]["valid"])
        self.assertEqual(sum(d["detail"] == "vendor_gstin" for d in r["details"]), 1)

    def test_the_wide_view_matches_the_detail_list(self):
        from invoice_extractor.organizer import DETAILS

        sql = (ROOT / "database" / "migrations" / "009_invoice_attributes.sql").read_text()
        for _, key, _, _, _ in DETAILS:
            self.assertIn(f"a.detail = '{key}'", sql)


if __name__ == "__main__":
    unittest.main()
