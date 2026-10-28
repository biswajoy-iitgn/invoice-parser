"""Document profiles: config loading and validation, the generated reader contract, and cited/validated assembly.

The reader is stubbed; ingest and citation run for real on a small PDF with a text layer.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AUTH", "off")   # these test the API itself; sign-in is tested in test_auth.py
os.environ.setdefault("MODEL_POOL", "off")   # unit tests never load a model
os.environ.setdefault("DOC_PARSER", "off")

import fitz  # noqa: E402

from invoice_extractor import pipeline, profiles  # noqa: E402

GATE_PASS = ["MATERIAL GATE PASS - RETURNABLE", "Gate Pass No. GP/25/7890   Date 02/05/2026", "Vehicle No. GJ-33-SN-5969   Driver Name Imran Shaikh",
             "Issued By Vijay Rathod", "Sr  Description of Material   Qty  Unit", "1   Bearing 6205-2RS   17   Nos", "2   Calibration gauge set   35   Set"]
READER = {"gate_pass_no": "GP/25/7890", "date": "02/05/2026", "vehicle_no": "GJ-33-SN-5969", "driver_name": "Imran Shaikh", "driver_mobile": "",
          "department": "", "destination": "", "issued_by": "Vijay Rathod", "pass_type": "RETURNABLE", "expected_return": "",
          "items": [{"Description": "Bearing 6205-2RS", "Qty": "17", "Unit": "Nos", "lines": ["L6"]},
                    {"Description": "Calibration gauge set", "Qty": "35", "Unit": "Set", "lines": ["L7"]}],
          "evidence": {"gate_pass_no": ["L2"], "vehicle_no": ["L3"], "issued_by": ["L4"]}}


def make_pdf(lines):
    """A one-page PDF with a text layer. A footer pads it past the 80-character threshold below which the pipeline OCRs."""
    doc = fitz.open()
    page = doc.new_page()
    for i, t in enumerate(lines):
        page.insert_text((40, 60 + 18 * i), t, fontsize=10)
    page.insert_text((40, 800), "Generated test document footer text for the profile tests", fontsize=8)
    path = os.path.join(tempfile.mkdtemp(), "doc.pdf")
    doc.save(path)
    return path


class ProfileConfigTests(unittest.TestCase):
    def test_shipped_profiles_load(self):
        ps = profiles.load_all()
        self.assertEqual({"logistics_invoice", "quality_report", "mill_certificate", "gate_pass", "roster", "specialised_table", "general"}, set(ps))
        self.assertEqual("generic", ps["logistics_invoice"].engine)        # invoices are a field profile; no fine-tuned adapter
        self.assertTrue(all(p.detect for k, p in ps.items() if k not in ("general", "specialised_table")))

    def test_bad_profile_names_its_file(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "x.yaml").write_text("id: x\nname: X\nfields:\n  - {key: a, label: A, type: banana}\n")
            with patch.dict(os.environ, {"PROFILES_DIR": d}), self.assertRaisesRegex(ValueError, r"x\.yaml: field a: type"):
                profiles.load_all()
            Path(d, "x.yaml").write_text("id: x\nname: X\ntables:\n  - {key: t, label: T, columns: [A]}\nchecks:\n  - {type: sum, table: t, column: B, field: f}\n")
            with patch.dict(os.environ, {"PROFILES_DIR": d}), self.assertRaisesRegex(ValueError, "one of the table's columns"):
                profiles.load_all()

    def test_a_new_file_is_a_new_document_type(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "memo.yaml").write_text("id: memo\nname: Memo\nfields:\n  - {key: subject, label: Subject, required: true}\n")
            with patch.dict(os.environ, {"PROFILES_DIR": d}):
                self.assertIn("subject", profiles.json_schema(profiles.get("memo"))["properties"])
                with self.assertRaises(KeyError):
                    profiles.get("nope")

    def test_prompt_and_schema_follow_the_profile(self):
        p = profiles.get("gate_pass")
        prompt, schema = profiles.system_prompt(p), profiles.json_schema(p)
        for f in p.fields:
            self.assertIn(f.key, prompt)
            self.assertIn(f.key, schema["properties"])
        self.assertEqual(["", "RETURNABLE", "NON-RETURNABLE"], schema["properties"]["pass_type"]["enum"])
        self.assertEqual(["Description", "Qty", "Unit", "lines"], list(schema["properties"]["items"]["items"]["properties"]))


class ProfileExtractTests(unittest.TestCase):
    def run_profile(self, reader, name="gate_pass", lines=GATE_PASS):
        seen = {}

        def fake(text, model=None, system=None, schema=None, images=None):
            seen.update(system=system, schema=schema)
            return reader

        with patch.object(pipeline, "call_reader", fake):
            res, audit = profiles.extract(make_pdf(lines), name)
        return res, seen

    def test_cited_values_and_cells(self):
        res, seen = self.run_profile(READER)
        self.assertIn("Gate pass slip", seen["system"])
        self.assertEqual("verified", res["fields"]["vehicle_no"]["status"])
        self.assertTrue(res["fields"]["vehicle_no"]["boxes"])
        self.assertEqual("missing", res["fields"]["department"]["status"])
        self.assertEqual(1.0, res["tables"]["items"]["verified"])
        self.assertEqual([], res["reasons"])
        self.assertEqual("REVIEW", res["decision"])       # auto_accept is off unless the profile turns it on

    def test_invented_or_malformed_values_go_to_review(self):
        res, _ = self.run_profile({**READER, "vehicle_no": "GJ-01-AB-1234", "date": "someday",
                                   "items": [{"Description": "Gold bars", "Qty": "99", "Unit": "Nos", "lines": ["L6"]}]})
        text = " | ".join(res["reasons"])
        self.assertEqual("unverified", res["fields"]["vehicle_no"]["status"])
        self.assertIn("vehicle_no: value not found", text)
        self.assertIn("date: not a recognised date", text)
        self.assertIn("items: only 33% of cells", text)

    def test_missing_required_and_bad_choice(self):
        res, _ = self.run_profile({**READER, "gate_pass_no": "", "pass_type": "MAYBE"})
        text = " | ".join(res["reasons"])
        self.assertIn("gate_pass_no: required value not printed", text)
        self.assertIn("pass_type: not one of", text)

    def test_sum_check(self):
        lines = ["Department R and D   Weekly Total Pay 3,000.00", "E1001  Ann   Tech   $45.00   40   1,800.00", "E1002  Bob   Tech   $50.00   24   1,200.00"]
        raw = {"department": "R and D", "week_start": "", "scheduler": "", "total_hours": "", "total_pay": "3,000.00",
               "employees": [{"ID": "E1001", "Name": "Ann", "Role": "Tech", "Hourly Rate": "$45.00", "Total Hours": "40", "Total Pay": "1,800.00", "lines": ["L2"]},
                             {"ID": "E1002", "Name": "Bob", "Role": "Tech", "Hourly Rate": "$50.00", "Total Hours": "24", "Total Pay": "1,200.00", "lines": ["L3"]}],
               "evidence": {"department": ["L1"], "total_pay": ["L1"]}}
        res, _ = self.run_profile(raw, "roster", lines)
        self.assertEqual([], res["reasons"])
        raw["total_pay"] = "3,500.00"
        res, _ = self.run_profile(raw, "roster", lines)
        self.assertTrue(any("rows add up to 3000; total_pay is 3500" in r for r in res["reasons"]), res["reasons"])

    def test_printed_characters_win(self):
        lines = ["Report 0445661   Customer ACME", "C 0,047   Si 0,017"]
        p = profiles.Profile(id="t", name="T", fields=[profiles.Field("report_no", "Report", "id", True)],
                             tables=[profiles.Table("r", "R", ["Element", "Value"])])
        raw = {"report_no": "0445661", "r": [{"Element": "C", "Value": "0.047", "lines": ["L2"]}], "evidence": {"report_no": ["L1"]}}
        with patch.object(pipeline, "call_reader", lambda *a, **k: raw):
            res, _ = profiles.extract(make_pdf(lines), p)
        self.assertEqual("0,047", res["tables"]["r"]["rows"][0]["cells"]["Value"])     # the VLM wrote a point, the page has a comma
        self.assertEqual([], res["reasons"])

    def test_the_roster_pay_formula_is_checked_row_by_row(self):
        lines = ["Department R and D   Weekly Total Pay 2,900.00", "E1001  Ann   Tech   $45.00   40   1,800.00", "E1002  Bob   Tech   $50.00   22   1,100.00"]
        raw = {"department": "R and D", "week_start": "", "scheduler": "", "total_hours": "", "total_pay": "2,900.00",
               "employees": [{"ID": "E1001", "Name": "Ann", "Role": "Tech", "Hourly Rate": "$45.00", "Total Hours": "40", "Total Pay": "1,800.00", "lines": ["L2"]},
                             {"ID": "E1002", "Name": "Bob", "Role": "Tech", "Hourly Rate": "$50.00", "Total Hours": "22", "Total Pay": "1,100.00", "lines": ["L3"]}],
               "evidence": {"department": ["L1"], "total_pay": ["L1"]}}
        res, _ = self.run_profile(raw, "roster", lines)
        self.assertEqual([], res["reasons"])
        by = {c["name"]: c["ok"] for c in res["checks"]}
        self.assertTrue(by["pay is rate times hours"])
        self.assertFalse(any(v is False for v in by.values()))       # the hours total is blank on this page: that check could not run, it did not fail
        raw["employees"][1]["Total Pay"] = "1,010.00"                # 22 x 50 is 1,100
        raw["total_pay"] = "2,810.00"
        res, _ = self.run_profile(raw, "roster", lines)
        self.assertTrue(any("pay is rate times hours" in r and "row 2: Total Pay printed 1010, the formula gives 1100" in r for r in res["reasons"]), res["reasons"])

    def test_typed_identifiers_and_dates_are_validated(self):
        res, _ = self.run_profile({**READER, "vehicle_no": "ZZ-33-SN-5969", "driver_mobile": "948445258", "date": "02/05/2099",
                                   "evidence": {"vehicle_no": ["L3"]}})
        f = res["fields"]
        self.assertEqual("invalid", f["vehicle_no"]["status"])
        self.assertIn("not a state code", f["vehicle_no"]["issue"])
        self.assertEqual("invalid", f["driver_mobile"]["status"])
        self.assertIn("not a plausible date", f["date"]["issue"])
        names = {c["name"]: c["ok"] for c in res["checks"]}
        self.assertFalse(names["pass date is recent and not in the future"])

    def test_the_general_profile_validates_identifiers_by_their_label(self):
        lines = ["GSTIN 24AAACK2676H1ZN   Vehicle No. GJ-33-SN-5969", "Invoice Date 01.05.202X   Email info@harsha.co.in", "Remarks"]
        raw = {"document_type": "invoice", "title": "", "tables": [], "fields": [
            {"label": "GSTIN", "value": "24AAACK2676H1ZN", "lines": ["L2"]}, {"label": "Vehicle No.", "value": "GJ-33-SN-5969", "lines": ["L2"]},
            {"label": "Invoice Date", "value": "01.05.202X", "lines": ["L3"]}, {"label": "Email", "value": "info@harsha.co.in", "lines": ["L3"]}]}
        with patch.dict(os.environ, {"DOC_PARSER": "off"}), patch.object(pipeline, "call_reader", lambda *a, **k: raw):
            res, _ = profiles.extract(make_pdf(lines), "general")
        by = {f["label"]: f for f in res["fields"]}
        self.assertEqual("invalid", by["GSTIN"]["status"])
        self.assertIn("check character should be M", by["GSTIN"]["issue"])
        self.assertEqual("invalid", by["Invoice Date"]["status"])
        self.assertNotEqual("invalid", by["Vehicle No."]["status"])
        self.assertNotEqual("invalid", by["Email"]["status"])
        self.assertTrue(any(r.startswith("GSTIN is a valid gstin") for r in res["reasons"]), res["reasons"])
        self.assertEqual(4, sum("is a valid" in c["name"] for c in res["checks"]))

    def test_number_parsing(self):
        self.assertEqual(1800.0, profiles._number("$1,800.00"))
        self.assertEqual(0.047, profiles._number("0,047"))
        self.assertEqual(1234.5, profiles._number("1.234,50"))
        self.assertEqual(1800.0, profiles._number("1,800"))
        self.assertEqual(1234.5, profiles._number("1.234,5"))
        self.assertIsNone(profiles._number("n/a"))
        from invoice_extractor.checks import number
        self.assertEqual(1800.0, number("Rs. 1,800.00"))
        self.assertEqual(1800.0, profiles._number("Rs. 1,800.00"))
        for code in ("EN10130:2006", "2T22226224", "12/03/2024"):      # a misplaced code is not a number to check
            self.assertIsNone(number(code), code)


class ProfileModelsAndApiTests(unittest.TestCase):
    def setUp(self):
        import shutil

        self.dir = tempfile.mkdtemp()
        for f in (ROOT / "configs" / "profiles").glob("*.yaml"):
            shutil.copy(f, self.dir)
        patcher = patch.dict(os.environ, {"PROFILES_DIR": self.dir, "DOC_PARSER": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_and_what_each_engine_can_switch(self):
        d = profiles.describe(profiles.get("general"))
        self.assertEqual({"reader": "qwen3-vl-4b", "tables": "document-parser"}, d["models"])
        self.assertEqual({"reader": "qwen3-vl-4b"}, profiles.describe(profiles.get("gate_pass"))["models"])
        self.assertEqual({"reader": "qwen3-vl-4b"}, profiles.describe(profiles.get("logistics_invoice"))["models"])
        self.assertEqual({"reader": "nuextract3"}, profiles.describe(profiles.get("roster"))["models"])   # dense grid: NuExtract3 (reader bake-off)
        self.assertEqual(["reader", "tables"], d["editable_models"])
        self.assertEqual(["reader"], profiles.describe(profiles.get("logistics_invoice"))["editable_models"])
        self.assertEqual(["reader"], profiles.describe(profiles.get("gate_pass"))["editable_models"])

    def test_save_models_changes_only_the_models_block(self):
        path = Path(self.dir, "general.yaml")
        before = path.read_text()
        profiles.save_models("general", {"reader": "qwen3-vl-30b-a3b", "tables": "reader"})
        profiles.save_models("general", {"tables": "off"})          # a second save replaces, never duplicates
        after = path.read_text()
        self.assertEqual(1, after.count("models:"))
        self.assertTrue(after.startswith(before.rstrip()))           # comments and the rest untouched
        self.assertEqual({"reader": "qwen3-vl-30b-a3b", "tables": "off"}, profiles.get("general").models)
        with self.assertRaises(ValueError):
            profiles.save_models("general", {"reader": "nope"})
        with self.assertRaises(ValueError):
            profiles.save_models("gate_pass", {"tables": "off"})     # a field-bound profile has no table reader to switch
        self.assertEqual({"reader": "qwen3-vl-30b-a3b", "tables": "off"}, profiles.get("general").models)

    def test_the_chosen_models_are_used(self):
        profiles.save_models("general", {"reader": "qwen3-vl-30b-a3b", "tables": "off"})
        seen = {}

        def fake(text, model=None, system=None, schema=None, images=None):
            seen["model"] = model
            return GeneralProfileTests.RAW

        with patch.object(pipeline, "call_reader", fake):
            res, audit = profiles.extract(make_pdf(GeneralProfileTests.LINES), "general")
        self.assertEqual("qwen3-vl-30b-a3b", seen["model"])                 # the catalog id; the pool maps it to its files
        self.assertEqual([], res["tables"])                          # tables off
        self.assertEqual({"reader": seen["model"], "tables": "off"}, audit["models"])

    def test_api(self):
        from fastapi.testclient import TestClient

        from invoice_extractor import service

        c = TestClient(service.app, raise_server_exceptions=False)
        ps = c.get("/api/profiles").json()["profiles"]
        self.assertEqual(7, len(ps))
        self.assertIn("qwen3-vl-8b", [m["id"] for m in c.get("/api/models").json()["readers"]])
        r = c.put("/api/profiles/general/models", json={"tables": "reader"})
        self.assertEqual("reader", r.json()["models"]["tables"])
        self.assertEqual(422, c.put("/api/profiles/general/models", json={"reader": "nope"}).status_code)
        self.assertEqual(404, c.put("/api/profiles/zzz/models", json={}).status_code)
        pdf = Path(make_pdf(GATE_PASS)).read_bytes()
        with patch.object(pipeline, "call_reader", lambda *a, **k: READER):
            ok = c.post("/api/profiles/gate_pass/extract", files={"file": ("gp.pdf", pdf, "application/pdf")})
        self.assertEqual(200, ok.status_code, ok.text)
        body = ok.json()
        self.assertEqual("verified", body["result"]["fields"]["vehicle_no"]["status"])
        self.assertNotIn("reader_raw", body["audit"])
        self.assertEqual(404, c.post("/api/profiles/zzz/extract", files={"file": ("a.pdf", pdf, "application/pdf")}).status_code)


if __name__ == "__main__":
    unittest.main()


class GeneralProfileTests(unittest.TestCase):
    LINES = ["TEST CERTIFICATE", "Customer ACME SRL   Order 2217210", "Heat  C  Si", "H1   0,047   0,017", "H2   0,035   0,009"]
    RAW = {"document_type": "certificate", "title": "TEST CERTIFICATE",
           "fields": [{"label": "Customer", "value": "ACME SRL", "lines": ["L2"]}, {"label": "Order", "value": "2217210", "lines": ["L2"]},
                      {"label": "Approved by", "value": "Nobody Printed", "lines": ["L2"]}],
           "tables": [{"title": "", "columns": ["Heat", "C", "Si"], "rows": [["H1", "0.047", "0,017"], ["H2", "0,035", "0,009"]], "lines": ["L4", "L5", "L6"]}]}

    def read(self, raw=None, parsed=None):
        with patch.dict(os.environ, {"DOC_PARSER": "off"}), patch.object(pipeline, "call_reader", lambda *a, **k: raw or self.RAW):
            return profiles.extract(make_pdf(self.LINES), "general")

    def test_reads_fields_tables_and_text_with_no_field_list(self):
        res, audit = self.read()
        self.assertEqual("certificate", res["document_type"])
        self.assertEqual("ACME SRL", next(f for f in res["fields"] if f["label"] == "Customer")["value"])
        self.assertEqual("unverified", next(f for f in res["fields"] if f["label"] == "Approved by")["status"])   # not on the page
        t = res["tables"][0]
        self.assertEqual(["Heat", "C", "Si"], t["columns"])
        self.assertEqual(["H1", "0,047", "0,017"], t["rows"][0])              # printed comma restored
        self.assertEqual(1.0, t["verified"])
        self.assertIn("Customer", res["text"][0]["text"])
        self.assertEqual("REVIEW", res["decision"])

    def test_invented_table_is_flagged(self):
        raw = {**self.RAW, "tables": [{"title": "", "columns": ["A", "B"], "rows": [["x1", "y1"], ["x2", "y2"]], "lines": ["L4"]}]}
        res, _ = self.read(raw)
        self.assertTrue(any(r.startswith("table 1: only") for r in res["reasons"]), res["reasons"])

    def test_a_cell_is_checked_inside_its_own_table(self):
        lines = ["CERTIFICATE ACC. TO EN 10204 / 3.1", "Customer ACME SRL", "Heat   Elong", "H1   31", "H2   36"]
        path = make_pdf(lines)
        pages = pipeline.load_pages(path)
        index = pipeline.serialize([(pg.number, pg.units) for pg in pages])[1]
        table = {"title": "", "columns": ["Heat", "Elong"], "rows": [["H1", "3.1"], ["H2", "36"]], "lines": [], "page": 1, "bbox": [30, 100, 400, 140]}
        res = profiles.assemble_general(profiles.get("general"), {"fields": [], "tables": []}, index, pages, [table])
        self.assertEqual([["H1", "31"], ["H2", "36"]], res["tables"][0]["rows"])     # 3.1 printed in the title did not vouch for it
        self.assertEqual(1.0, res["tables"][0]["verified"])
        table["rows"] = [["H1", "99"], ["H2", "36"]]
        res = profiles.assemble_general(profiles.get("general"), {"fields": [], "tables": []}, index, pages, [table])
        self.assertLess(res["tables"][0]["verified"], 1.0)

    def test_low_ocr_confidence_and_a_cut_off_reader_are_flagged(self):
        from invoice_extractor.layout import Line, Unit

        def line(n, text, score):
            u = Unit(text, (10.0, 10.0 * n, 200.0, 10.0 * n + 9), 1, score)
            return Line(f"L{n}", 1, text, u.box, [u])

        index = {"L1": line(1, "ASPL COIL NO. 260405)", 0.9523), "L2": line(2, "Customer ACME", 0.99)}
        raw = {"document_type": "x", "title": "", "tables": [], "_truncated": True,
               "fields": [{"label": "Coil", "value": "260405)", "lines": ["L1"]}, {"label": "Customer", "value": "ACME", "lines": ["L2"]}]}
        pages = [type("P", (), {"number": 1, "source": "ppocr"})()]
        res = profiles.assemble_general(profiles.get("general"), raw, index, pages)
        by = {f["label"]: f for f in res["fields"]}
        self.assertEqual("unverified", by["Coil"]["status"])           # found in the OCR text, but the OCR itself was doubtful
        self.assertIn("0.95", by["Coil"]["issue"])
        self.assertEqual("verified", by["Customer"]["status"])
        self.assertTrue(any(r.startswith("low OCR confidence") and "Coil" in r for r in res["reasons"]), res["reasons"])
        self.assertTrue(any("ran out of room" in r for r in res["reasons"]))

    def test_a_looping_reader_is_collapsed_and_flagged(self):
        raw = {**self.RAW, "fields": self.RAW["fields"] + [{"label": "Customer", "value": "ACME SRL", "lines": ["L2"]}] * 20}
        res, _ = self.read(raw)
        self.assertEqual(1, sum(f["label"] == "Customer" for f in res["fields"]))
        self.assertTrue(any("repeated itself (20 repeats dropped)" in r for r in res["reasons"]), res["reasons"])

    def test_the_image_reread_settles_doubtful_values(self):
        def field(label, value, page=1):
            return {"label": label, "value": value, "status": "unverified", "issue": "read with low OCR confidence (0.95); check it on the page",
                    "boxes": [{"page": page, "bbox": [40.0, 100.0, 120.0, 112.0]}]}

        result = {"fields": [field("ASPL COIL NO.", "260405)"), field("RECV.DATE", "11-6-26"), field("VERIFIED SIGN", "ce"), field("Gone", "x")],
                  "reasons": ["low OCR confidence (handwriting, stamps or smudges): ASPL COIL NO., RECV.DATE, VERIFIED SIGN, Gone"], "decision": "REVIEW"}
        reads = {"ASPL COIL NO.": "2604051", "RECV.DATE": "11-6-26", "VERIFIED SIGN": "", "Gone": "boom"}

        def fake(png, label, model):
            self.assertTrue(png.startswith(b"\x89PNG"))
            if reads[label] == "boom":
                raise RuntimeError("vision model offline")
            return reads[label]

        res = profiles.reread(make_pdf(self.LINES), result, "m", read=fake)
        by = {f["label"]: f for f in res["fields"]}
        self.assertEqual("verified", by["RECV.DATE"]["status"])            # the image agrees with the OCR text
        self.assertNotIn("issue", by["RECV.DATE"])
        self.assertEqual("2604051", by["ASPL COIL NO."]["vision_read"])    # the image disagrees: both are shown
        self.assertEqual("unverified", by["ASPL COIL NO."]["status"])
        self.assertIn("260405)", " ".join(res["reasons"]))
        self.assertIn("2604051", " ".join(res["reasons"]))
        self.assertIn("vision_error", by["Gone"])                          # an offline vision model changes nothing
        self.assertEqual({"agreed": 1, "differ": 1}, res["reread"])
        self.assertTrue(any(r.startswith("low OCR confidence") and "VERIFIED SIGN" in r and "RECV.DATE" not in r for r in res["reasons"]))

    def test_crops_come_from_pdfs_and_images(self):
        from PIL import Image

        path = make_pdf(self.LINES)
        self.assertTrue(profiles._crop(path, 1, [40.0, 100.0, 120.0, 112.0]).startswith(b"\x89PNG"))
        img = os.path.join(tempfile.mkdtemp(), "scan.png")
        Image.new("RGB", (600, 400), "white").save(img)
        png = profiles._crop(img, 1, [100.0, 100.0, 180.0, 110.0])
        import io

        self.assertGreaterEqual(Image.open(io.BytesIO(png)).height, 96)   # a thin crop is enlarged for reading

    def test_truncated_reader_output_is_repaired(self):
        from invoice_extractor.reader import parse_output

        r = parse_output('{"fields":[{"label":"A","value":"1","lines":["L1"]},{"label":"B","value":"say \\"hi\\"","lines":["L2"]},{"label":"C","value":"cut o')
        self.assertTrue(r["_truncated"])
        self.assertEqual(["A", "B"], [f["label"] for f in r["fields"]])
        self.assertEqual({"a": 1}, parse_output('{"a":1}'))

    def test_parser_tables_replace_the_readers(self):
        parsed = [{"title": "", "columns": ["Heat", "C", "Si"], "rows": [["H1", "0.047", "0.017"]], "lines": []}]
        from invoice_extractor import pipeline as pl

        lines = [pl.serialize([(pg.number, pg.units) for pg in pl.load_pages(make_pdf(self.LINES))])][0][1]
        pages = pl.load_pages(make_pdf(self.LINES))
        res = profiles.assemble_general(profiles.get("general"), self.RAW, lines, pages, parsed)
        self.assertEqual("document-parser", res["tables"][0]["source"])
        self.assertEqual(["H1", "0,047", "0,017"], res["tables"][0]["rows"][0])
