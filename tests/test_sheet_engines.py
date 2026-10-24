"""Sheet-engine contracts: MinerU/Paddle/Docling parses map onto exactly OUTPUT_FIELDS.

Fixture-based only (no MinerU server, no VL model, no Docling convert): pins
the field mapping, charge classification and the exact output column set the
sheet renders.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor import docling_engine, mineru_engine, paddle_engine, ppdoclayout_engine, sheet_fields, textboxes  # noqa: E402
from invoice_extractor.extract import citations_reading  # noqa: E402
from invoice_extractor.paddle_vl_worker import _layout_regions, map_layout_label  # noqa: E402
from invoice_extractor.pipeline import facts_reading  # noqa: E402
from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402

MINERU_MD = """\
DACHSER INDIA PVT. LTD.

| Document No. | Customer No. | Date |
| --- | --- | --- |
| 7044012289 | 46706855 | 21-08-2026 |
| H B/L No./HAWB No. | M B/L No. / MAWB No. | Vessel/ Flight |
| 70430050554 | HLCUBO12608AWQG7 | Norfolk Express |

| Product Information |
| --- |
| Pieces Nature of Goods 48 FCL: HLXU8364248 / 40' High Cube / 10669.000 kg / 40.000 cbm |

<table><tbody><tr><td>No.</td><td>Charge Code</td><td>Amount</td><td>Tax</td></tr>\
<tr><td>1031</td><td>Seal Fee FCL P.O.L 996759</td><td>1,120.00</td><td>B</td></tr>\
<tr><td>5000</td><td>Sea freight FCL 996521 - USD Exchange rate: 96.12000</td><td>5490.00 USD 527,698.80</td><td>C</td></tr>\
<tr><td>5290</td><td>THC port of loading 996759</td><td>17,500.00</td><td>B</td></tr></tbody></table>
"""

PADDLE_PAGES = [{
    "page_index": 0,
    "blocks": [
        {"label": "header", "content": "DACHSER INDIA PVT. LTD.", "bbox": []},
        {"label": "table", "content": (
            "<table><tr><td>Document No.\\n7044012289</td>"
            "<td>Date\\n21-08-2026</td></tr>"
            "<tr><td>H B/L No./HAWB No.\\n70430050554</td>"
            "<td>M B/L No. / MAWB No.\\nHLCUBO12608AWQG7</td></tr></table>"), "bbox": []},
        {"label": "table", "content": (
            "<table><tr><td>No.</td><td>Charge Code</td><td>HSN</td><td>Base 1</td>"
            "<td>Base 2</td><td>Amount INR</td><td>Tax</td></tr>"
            "<tr><td>1031</td><td>Seal Fee FCL P.O.L</td><td>996759</td><td></td><td></td>"
            "<td>1,120.00</td><td>B</td></tr>"
            "<tr><td>5000</td><td>Sea freight FCL\\n- USD Exchange rate: 96.12000</td>"
            "<td>996521</td><td></td><td>5490.00 USD</td><td>527,698.80</td><td>C</td></tr>"
            "</table>"), "bbox": []},
        {"label": "text", "content": "FCL: HLXU8364248 / 40' High Cube / 10669.000 kg / 40.000 cbm",
         "bbox": []},
    ],
}]


class SheetMapping(unittest.TestCase):
    def test_output_field_set_is_exact(self):
        self.assertEqual(len(OUTPUT_FIELDS), 31)
        fields = sheet_fields.build_fields({}, [], "")
        self.assertEqual(list(fields), OUTPUT_FIELDS)
        self.assertTrue(all(v == "" for v in fields.values()))

    def test_charge_classification_uses_schema_glossary(self):
        self.assertEqual(sheet_fields.classify_charge("Seal Fee FCL P.O.L"), "Seal Charges")
        self.assertEqual(sheet_fields.classify_charge("ENS charge FCL"), "ENS Charges")
        self.assertEqual(sheet_fields.classify_charge("Sea freight FCL"), "Sea Freight")
        self.assertEqual(sheet_fields.classify_charge("THC port of loading"), "Terminal Handling Charges")
        # DACHSER ground truth + human-verified paddle extraction agree: bare
        # "carrier charges origin" is Other Charges; only an explicit haulage /
        # pre-carriage cue (incl. DACHSER's "Haulage (delivery)") maps to
        # Pre Carriage Haulage.
        self.assertEqual(sheet_fields.classify_charge("Carrier charges origin"), "Other Charges")
        self.assertEqual(sheet_fields.classify_charge("Haulage (delivery)"), "Pre Carriage Haulage")
        self.assertEqual(sheet_fields.classify_charge("OTHER CHARGESTHC"), "Terminal Handling Charges")
        self.assertEqual(sheet_fields.classify_charge("OTHER CHARGESENS"), "ENS Charges")
        self.assertEqual(sheet_fields.classify_charge("Seafreight"), "Sea Freight")
        self.assertEqual(sheet_fields.classify_charge("B/L Fee FCL"), "BL Charges")
        self.assertEqual(sheet_fields.classify_charge("Hamali Charges"), "Handling Charges")
        self.assertEqual(sheet_fields.classify_charge("Quantum Flux Surcharge"), "Other Charges")

    def test_charge_totals_skip_total_rows(self):
        totals = sheet_fields.charges_from_items([
            {"description": "Sea freight FCL", "amount_inr": 527698.80},
            {"description": "Net Total", "amount_inr": 875852.30},
            {"description": "SGST Tax INR", "amount_inr": 44526.29},
            {"description": "Mystery line", "amount_inr": ""},
        ])
        self.assertEqual(totals, {"Sea Freight": "527698.80"})

    def _assert_invoice(self, fields):
        self.assertEqual(list(fields), OUTPUT_FIELDS)
        self.assertEqual(fields["Invoice No"], "7044012289")
        self.assertEqual(fields["Invoice Date"], "21-08-2026")
        self.assertEqual(fields["Container Number"], "HLXU8364248")
        self.assertEqual(fields["MAWB No"], "HLCUBO12608AWQG7")
        self.assertEqual(fields["HAWB No"], "70430050554")
        self.assertIn("DACHSER", fields["Vendor Name"])
        self.assertEqual(fields["Bill of Lading / HBL"], "HBL 70430050554; MBL HLCUBO12608AWQG7")
        self.assertEqual(fields["WEIGHT"], "10669.000 kg")
        self.assertEqual(fields["Sea Freight"], "527698.80")
        self.assertEqual(fields["Seal Charges"], "1120.00")
        self.assertEqual(fields["Ex Rate"], "USD 96.12000")
        self.assertEqual(fields["Air Freight"], "")

    def test_mineru_markdown_maps_to_sheet(self):
        kv, items, text, vendor, segments, extra = mineru_engine.reduce_markdown(MINERU_MD)
        fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
        self._assert_invoice(fields)
        self.assertEqual(fields["Terminal Handling Charges"], "17500.00")

    def test_weight_survives_merged_weight_volume_tokens(self):
        # live MinerU output: "10669.000 10669 40.000 kg / 40.000 cbm"
        text = ("48 FCL: HLXU8364248 / 40' High Cube / 48 Pieces / "
                "10669.000 10669 40.000 kg / 40.000 cbm")
        out = sheet_fields.headers_from_kv({}, text)
        self.assertEqual(out["WEIGHT"], "10669.000 kg")
        self.assertEqual(out["Container Number"], "HLXU8364248")

    def test_weight_prefers_explicit_weight_number(self):
        # glued single-line table: WEIGHT9895.000 KG beside big charge totals
        text = ("IMPORT CUSTOMS BROKERJOHN S JAMES WEIGHT9895.000 KG VOLUME "
                "CHARGEABLE PACKAGES43 PKG CHARGES 7,45,349.07 CHARGES IN INR 7,09,856.25")
        self.assertEqual(sheet_fields.weight_from_text(text), "9895.000 kg")

    def test_bare_freight_follows_transport_mode(self):
        self.assertEqual(sheet_fields.classify_charge("Freight USD 7,225.00 @ 98.250000", "sea"),
                         "Sea Freight")
        self.assertEqual(sheet_fields.classify_charge("Freight USD 7,225.00 @ 98.250000", "air"),
                         "Air Freight")
        self.assertEqual(sheet_fields.classify_charge("Freight USD 7,225.00 @ 98.250000"), "Other Charges")
        self.assertEqual(sheet_fields.transport_mode("VESSEL MSC ELAINE / OCEAN BILL OF LADING"), "sea")

    def test_heading_invoice_no_and_ocean_bl_fallbacks(self):
        text = ("## TAX INVOICE IN1AMD261031252\n"
                "OCEAN BILL OF LADINGMEDUAEO12208 HOUSE BILL OF LADINGAID0019575")
        out = sheet_fields.headers_from_kv({}, text)
        self.assertEqual(out["Invoice No"], "IN1AMD261031252")
        self.assertEqual(out["MAWB No"], "MEDUAEO12208")
        self.assertEqual(out["HAWB No"], "AID0019575")
        self.assertEqual(out["Bill of Lading / HBL"], "HBL AID0019575; MBL MEDUAEO12208")

    def test_container_prefers_container_line(self):
        text = "SHIPMENT\nSAID0019575\nCONTAINERSFFAU4143091 (40HC - Forty foot high cube) / FCL"
        self.assertEqual(sheet_fields.container_from_text(text), "FFAU4143091")

    def test_sac_rows_and_at_rate(self):
        kv, items = sheet_fields.generic_html_kv_items([
            ["INVOICE DATE", "31-Aug-26"],
            ["[SAC: 996521]", "Freight USD 7,225.00 @ 98.250000", "7,45,349.07",
             "CGST2.5%=17,746.41", "7,09,856.25"],
            ["[SAC: 996799]", "THC (Terminal Handling Charge)", "18,632.20",
             "CGST 9%=1,421.10", "15,790.00"],
        ])
        self.assertEqual(kv, {"INVOICE DATE": "31-Aug-26"})
        self.assertEqual(items[0], {"description": "Freight USD 7,225.00 @ 98.250000",
                                    "amount_inr": 709856.25,
                                    "tax_code": "CGST2.5%=17,746.41"})
        fields = sheet_fields.build_fields(
            kv, items, "VESSEL X\nFreight USD 7,225.00 @ 98.250000", "DSV Air & Sea Pvt. Ltd.")
        self.assertEqual(fields["Invoice Date"], "31-Aug-26")
        self.assertEqual(fields["Sea Freight"], "709856.25")
        self.assertEqual(fields["Terminal Handling Charges"], "15790.00")
        self.assertEqual(fields["Ex Rate"], "USD 98.250000")

    def test_vendor_trims_tax_id_noise(self):
        self.assertEqual(
            sheet_fields.clean_vendor("DSV Air & Sea Pvt. Ltd. GST Identification No: 24AACCD3848A1Z5"),
            "DSV Air & Sea Pvt. Ltd.")
        self.assertEqual(sheet_fields.clean_vendor("SJeena Logistics ORIGINAL FOR RECIPIENT"),
                         "Jeena Logistics")

    def test_heading_inv_no_date_and_bl_no_fallbacks(self):
        text = ("## TAX INVOICE\n## INV NO : IVJCAMD270007254\n**DATE:19/8/2026**\n"
                "BL No     : 5836209")
        out = sheet_fields.headers_from_kv({}, text)
        self.assertEqual(out["Invoice No"], "IVJCAMD270007254")
        self.assertEqual(out["Invoice Date"], "19/8/2026")
        self.assertEqual(out["HAWB No"], "5836209")

    def test_docx_style_lines_and_two_col_charges(self):
        md = ("ACME LOGISTICS PVT. LTD.\nTAX INVOICE\nInvoice No: TEST-2026-001\n"
              "Date: 02-09-2026\nContainer: MSKU1234567\n\n"
              "| Sea freight FCL | 50000.00 |\n| --- | --- |\n"
              "| THC port of loading | 17500.00 |\n| Seal fee | 1120.00 |")
        kv, items, text, vendor, segments, extra = mineru_engine.reduce_markdown(md)
        fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
        self.assertEqual(fields["Invoice No"], "TEST-2026-001")
        self.assertEqual(fields["Invoice Date"], "02-09-2026")
        self.assertEqual(fields["Container Number"], "MSKU1234567")
        self.assertEqual(fields["Vendor Name"], "ACME LOGISTICS PVT. LTD.")
        self.assertEqual(fields["Sea Freight"], "50000.00")
        self.assertEqual(fields["Terminal Handling Charges"], "17500.00")
        self.assertEqual(fields["Seal Charges"], "1120.00")

    def test_tax_table_and_totals(self):
        codes, totals = sheet_fields.tax_info_from_grid([
            ["Tax", "Net INR", "S%", "GST/UTGST Tax INR", "%", "CGST Tax INR",
             "%", "IGST Tax INR", "Gross Total INR", "Texts"],
            ["C", "527,698.80", "2.5", "13,192.47", "2.5", "13,192.47",
             "", "", "554,083.74", ""],
            ["B", "348,153.50", "9.0", "31,333.82", "9.0", "31,333.82",
             "", "", "410,821.14", "freight top"],
            ["Gross Total 44,526.29", "44,526.29", "", "964,904.88", ""],
        ])
        self.assertEqual(set(codes), {"B", "C"})
        self.assertEqual(codes["C"]["net_inr"], 527698.80)
        self.assertEqual(codes["C"]["gross_inr"], 554083.74)
        self.assertEqual(codes["C"]["sgst_pct"], "2.5")
        self.assertEqual(codes["C"]["sgst_inr"], 13192.47)
        self.assertEqual(codes["B"]["texts"], "freight top")
        self.assertEqual(totals, {"gross_total_inr": "964904.88"})
        self.assertEqual(sheet_fields.tax_info_from_grid([["No.", "Charge"]]), ({}, {}))
        t = sheet_fields.totals_from_text("Net Total 875,852.30\nAMOUNT IN WORDS INR NINE ONLY")
        self.assertEqual(t["net_total_inr"], "875852.30")
        self.assertEqual(t["amount_in_words"], "NINE ONLY")

    def test_charge_rows_keep_tax_code(self):
        md = ("<table><tr><td>No.</td><td>Charge</td><td>Amount</td><td>Tax</td></tr>"
              "<tr><td>5000</td><td>Sea freight FCL</td><td>527,698.80</td><td>C</td></tr></table>")
        kv, items, text, vendor, segments, extra = mineru_engine.reduce_markdown(md)
        self.assertEqual(items[0]["tax_code"], "C")

    def test_output_columns_spelled_out(self):
        self.assertIn("Handling Charges", OUTPUT_FIELDS)
        self.assertIn("Air Waybill Charges", OUTPUT_FIELDS)
        self.assertNotIn("Handlign Charges", OUTPUT_FIELDS)
        self.assertNotIn("Airway WayBill Charges", OUTPUT_FIELDS)

    def test_glued_invoice_number_and_date_split(self):
        out = sheet_fields.headers_from_kv({"Invoice No": "AMD1142005 13.08.2026"}, "")
        self.assertEqual(out["Invoice No"], "AMD1142005")
        self.assertEqual(out["Invoice Date"], "13.08.2026")

    def test_document_date_satisfies_date(self):
        out = sheet_fields.headers_from_kv({"Document Date": "13/08/2026"}, "")
        self.assertEqual(out["Invoice Date"], "13/08/2026")
        out = sheet_fields.headers_from_kv({"Date": "21-08-2026", "Document Date": "13/08/2026"}, "")
        self.assertEqual(out["Invoice Date"], "13/08/2026")
        # a bare DATE from shipping context must not beat the document date
        out = sheet_fields.headers_from_kv({"DATE": "10-JUL-2026", "Document Date": "13/08/2026"}, "")
        self.assertEqual(out["Invoice Date"], "13/08/2026")
        # ...nor may a referenced document's invoice date
        out = sheet_fields.headers_from_kv({"INVOICE DATE": "08-07-2026", "Document Date": "13/08/2026"}, "")
        self.assertEqual(out["Invoice Date"], "13/08/2026")

    def test_s_no_grid_books_assessable_amount(self):
        kv, items = sheet_fields.generic_html_kv_items([
            ["S. No.", "Description / HSN / Is Service", "Assessable Amount (Rs.)",
             "CGST (Rs.)", "Total Amount (Rs.)"],
            ["1", "B/L Fee Export\\n00996719 Y", "15,300.00", "1,377.00\\n9.00%", "18,054.00"],
            ["6", "Seafreight\\n00996521 Y", "456,533.74", "11,413.34\\n2.50%", "479,360.42"],
        ])
        self.assertEqual(items, [
            {"description": "B/L Fee Export", "amount_inr": 15300.00},
            {"description": "Seafreight", "amount_inr": 456533.74},
        ])
        totals = sheet_fields.charges_from_items(items)
        self.assertEqual(totals["BL Charges"], "15300.00")
        self.assertEqual(totals["Sea Freight"], "456533.74")

    def test_colon_cells_and_hbl_no(self):
        self.assertEqual(sheet_fields.split_colon("Document No: AMD1142005"),
                         ("Document No", "AMD1142005"))
        out = sheet_fields.headers_from_kv({}, "HBL NO: BANQ1075870985")
        self.assertEqual(out["HAWB No"], "BANQ1075870985")
        self.assertEqual(
            sheet_fields.invoice_total_from_rows([["Total Invoice Amount (Rs.)", "558,170.15"]]),
            {"invoice_total_inr": "558170.15"})

    def test_footer_totals_subtotal_and_tax_splits(self):
        out = sheet_fields.invoice_total_from_rows([
            ["SGST 9%=2,682.90", ""],
            ["SUBTOTAL", "7,67,903.75"],
            ["ADD CGST", "22,970.69"],
            ["ADD SGST", "22,970.69"],
            ["TOTAL INR", "8,13,845.13"],
        ])
        self.assertEqual(out, {"subtotal_inr": "767903.75", "cgst_inr": "22970.69",
                               "sgst_inr": "22970.69", "invoice_total_inr": "813845.13"})

    def test_total_inr_never_books_as_charge(self):
        fields = sheet_fields.build_fields({}, [
            {"description": "TOTAL INR", "amount_inr": 813845.13},
            {"description": "ISPS", "amount_inr": 1965.00},
        ], "VESSEL X")
        self.assertEqual(fields["Other Charges"], "1965.00")
        lines = sheet_fields.line_items_for([
            {"description": "SUBTOTAL", "amount_inr": 767903.75},
            {"description": "ISPS", "amount_inr": 1965.00},
        ], "sea")
        self.assertEqual([ln["description"] for ln in lines], ["ISPS"])

    def test_docling_grids_map_to_sheet(self):
        def cell(r, c, text, header=False):
            return SimpleNamespace(start_row_offset_idx=r, start_col_offset_idx=c,
                                   text=text, column_header=header)
        ship = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 0, "Document No.", True), cell(0, 1, "Date", True),
            cell(1, 0, "7044012289"), cell(1, 1, "21-08-2026"),
        ]))
        charges = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 0, "No.", True), cell(0, 1, "Charge Code", True),
            cell(0, 2, "Amount INR", True), cell(0, 3, "Tax", True),
            cell(1, 0, "5000"), cell(1, 1, "Sea freight FCL"),
            cell(1, 2, "527,698.80"), cell(1, 3, "C"),
        ]))
        tax = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 0, "Tax", True), cell(0, 1, "Net INR", True),
            cell(0, 8, "Gross Total INR", True),
            cell(1, 0, "C"), cell(1, 1, "527,698.80"), cell(1, 8, "554,083.74"),
        ]))
        doc = SimpleNamespace(
            texts=[SimpleNamespace(text="DACHSER INDIA PVT. LTD."),
                   SimpleNamespace(text="FCL: HLXU8364248 / 10669.000 kg")],
            tables=[ship, charges, tax])
        kv, items, text, vendor, segments, extra = docling_engine.reduce_document(doc)
        fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
        self.assertEqual(fields["Invoice No"], "7044012289")
        self.assertEqual(fields["Invoice Date"], "21-08-2026")
        self.assertEqual(fields["Container Number"], "HLXU8364248")
        self.assertEqual(fields["WEIGHT"], "10669.000 kg")
        self.assertEqual(fields["Vendor Name"], "DACHSER INDIA PVT. LTD.")
        self.assertEqual(fields["Sea Freight"], "527698.80")
        self.assertEqual(items[0]["tax_code"], "C")
        self.assertEqual(extra["tax_codes"]["C"]["net_inr"], 527698.80)
        self.assertEqual(extra["tax_codes"]["C"]["gross_inr"], 554083.74)

    def test_glued_label_cells_split(self):
        self.assertEqual(sheet_fields.split_known_label("H B/L No./HAWB No. 70430050554"),
                         ("H B/L No./HAWB No.", "70430050554"))
        self.assertEqual(sheet_fields.split_known_label("Date 21-08-2026"), ("Date", "21-08-2026"))
        self.assertEqual(sheet_fields.split_known_label("Date"), ("", ""))
        self.assertEqual(sheet_fields.split_known_label("Seal Fee FCL"), ("", ""))

    def test_container_skips_ifsc(self):
        text = "IFSC: HSBC0400002\nFCL: HLXU8364248 / 40' High Cube"
        self.assertEqual(sheet_fields.container_from_text(text), "HLXU8364248")

    def test_textbox_variants(self):
        v = textboxes.variants("527698.80")
        self.assertIn("527698.80", v)
        self.assertIn("527,698.80", v)
        self.assertIn("5,27,698.80", v)
        v = textboxes.variants("HBL AID0019575; MBL MEDUAEO12208")
        self.assertIn("AID0019575", v)
        self.assertIn("MEDUAEO12208", v)
        self.assertEqual(textboxes.variants("24"), [])
        ex = textboxes.explained_for({"Invoice No": [{"page": 1, "text": "7044012289"}]})
        self.assertEqual(ex["Invoice No"]["basis"], "relocated")
        ex = textboxes.explained_for(
            {"Invoice No": [{"page": 1, "bbox": [1, 2, 3, 4], "text": "7044012289"}]})
        self.assertEqual(ex["Invoice No"]["citations"][0]["bbox"], [1, 2, 3, 4])

    def test_sr_ledger_rows_book_taxable_value(self):
        kv, items = sheet_fields.generic_html_kv_items([
            ["1", "LOADING & UNLOADING CHARGES", "996519", "0.00", "2,500.00",
             "9.0%", "225.00", "9.0%", "225.00", "", "0.00"],
            ["5", "CFS CHARGES", "998599", "0.00", "26,900.00",
             "9.0%", "2,421.00", "9.0%", "2,421.00", "", "0.00"],
            ["GRAND TOTAL", "0.00", "33,100.00", "", "2,979.00"],
        ])
        self.assertEqual(kv, {})
        self.assertEqual(items, [
            {"description": "LOADING & UNLOADING CHARGES", "amount_inr": 2500.00},
            {"description": "CFS CHARGES", "amount_inr": 26900.00},
        ])
        totals = sheet_fields.charges_from_items(items)
        self.assertEqual(totals, {"Handling Charges": "2500.00",
                                  "Warehouse charges Port": "26900.00"})

    def test_paddle_blocks_map_to_sheet(self):
        kv, items, text, vendor, segments, extra = paddle_engine.reduce_blocks(PADDLE_PAGES)
        fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
        self._assert_invoice(fields)


LAYOUT_PAGES = [{
    "page_index": 0,
    # reading-ordered blocks as the layout worker emits them
    "blocks": [
        {"label": "doc_title", "content": "DACHSER INDIA PVT. LTD.", "bbox": [50, 40, 400, 70]},
        {"label": "table", "content": (
            "<table><tr><td>Document No.\n7044012289</td>"
            "<td>Date\n21-08-2026</td></tr>"
            "<tr><td>H B/L No./HAWB No.\n70430050554</td>"
            "<td>M B/L No. / MAWB No.\nHLCUBO12608AWQG7</td></tr></table>"),
         "bbox": [50, 90, 550, 200]},
        {"label": "table", "content": (
            "<table><tr><td>No.</td><td>Charge Code</td><td>HSN</td><td>Base 1</td>"
            "<td>Base 2</td><td>Amount INR</td><td>Tax</td></tr>"
            "<tr><td>1031</td><td>Seal Fee FCL P.O.L</td><td>996759</td><td></td><td></td>"
            "<td>1,120.00</td><td>B</td></tr>"
            "<tr><td>5000</td><td>Sea freight FCL\n- USD Exchange rate: 96.12000</td>"
            "<td>996521</td><td></td><td>5490.00 USD</td><td>527,698.80</td><td>C</td></tr>"
            "</table>"), "bbox": [50, 220, 550, 500]},
        {"label": "text", "content": "FCL: HLXU8364248 / 40' High Cube / 10669.000 kg / 40.000 cbm",
         "bbox": [50, 520, 550, 560]},
    ],
}]


class PPDocLayoutEngine(SheetMapping):
    def test_layout_labels_map_to_reduce_vocabulary(self):
        self.assertEqual(map_layout_label("Table"), "table")
        self.assertEqual(map_layout_label("title"), "doc_title")
        self.assertEqual(map_layout_label("Doc_Title"), "doc_title")
        self.assertEqual(map_layout_label("header"), "header")
        self.assertEqual(map_layout_label("figure_caption"), "figure")
        self.assertEqual(map_layout_label("seal"), "figure")
        self.assertEqual(map_layout_label("text"), "text")
        self.assertEqual(map_layout_label("footer"), "text")
        self.assertEqual(map_layout_label(""), "text")

    def test_layout_regions_follow_reading_order(self):
        data = {"boxes": [
            {"coordinate": [10, 500, 200, 560], "label": "text", "reading_order": 2},
            {"coordinate": [[10, 10], [200, 10], [200, 60], [10, 60]],
             "label": "Title", "reading_order": 0},
            {"coordinate": [10, 100, 200, 400], "label": "table", "reading_order": 1},
        ]}
        regions = _layout_regions(data)
        self.assertEqual([r["label"] for r in regions], ["doc_title", "table", "text"])
        self.assertEqual(regions[1]["bbox"], [10, 100, 200, 400])
        self.assertEqual(regions[0]["bbox"], [10, 10, 200, 60])

    def test_layout_blocks_map_to_sheet(self):
        kv, items, text, vendor, segments, extra = paddle_engine.reduce_blocks(LAYOUT_PAGES)
        fields = sheet_fields.build_fields(kv, items, text, vendor, segments)
        self._assert_invoice(fields)

    def test_extract_uses_layout_client(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as fh:
            with mock.patch("invoice_extractor.paddle_vl_client.parse_layout",
                            return_value={"pages": LAYOUT_PAGES}) as pl:
                fields, audit = ppdoclayout_engine.extract(fh.name)
        pl.assert_called_once()
        self._assert_invoice(fields)
        self.assertEqual(audit["engine"], "ppdoclayout")

    def test_available_mirrors_vl_interpreter(self):
        self.assertIsInstance(ppdoclayout_engine.available(), bool)


class DetectionReading(unittest.TestCase):
    def test_paddle_blocks_reading_scales_boxes(self):
        pages = [{"page_index": 0, "blocks": [
            {"label": "table", "content": "<table><tr><td>Hi</td></tr></table>",
             "bbox": [100, 200, 300, 400]},
            {"label": "text", "content": "Hello", "bbox": []},
            {"label": "text", "content": "   ", "bbox": [1, 2, 3, 4]},
        ]}]
        reading = paddle_engine.blocks_reading(pages, is_pdf=True)
        self.assertEqual(len(reading), 2)
        self.assertEqual(reading[0]["bbox"], [50.0, 100.0, 150.0, 200.0])
        self.assertEqual(reading[0]["content"], "Hi")
        self.assertEqual(reading[1]["bbox"], [])
        img = paddle_engine.blocks_reading(pages, is_pdf=False)
        self.assertEqual(img[0]["bbox"], [100, 200, 300, 400])

    def test_mineru_md_reading_chunks(self):
        md = "# Header\n\nSome prose here.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        reading = mineru_engine.md_reading(md)
        labels = [r["label"] for r in reading]
        self.assertIn("table", labels)
        self.assertTrue(all(r["page"] is None for r in reading))
        self.assertTrue(all(r["content"] for r in reading))
        self.assertLessEqual(len(mineru_engine.md_reading("# T\n" + "x\n" * 200)), 60)

    def test_facts_reading_keeps_boxes(self):
        facts = [{"label": "Invoice No", "value": "X1", "page": 2,
                  "bbox": [1, 2, 3, 4], "seq": 1},
                 {"label": "", "value": "", "page": 1, "bbox": [1, 2, 3, 4]}]
        reading = facts_reading(facts)
        self.assertEqual(len(reading), 1)
        self.assertEqual(reading[0]["content"], "Invoice No: X1")
        self.assertEqual(reading[0]["bbox"], [1.0, 2.0, 3.0, 4.0])

    def test_citations_reading_skips_item_keys(self):
        cites = {"Invoice No": [{"page": 1, "bbox": [1, 2, 3, 4],
                                 "source_text": "X1", "score": 0.9}],
                 "__item_0": [{"page": 1, "bbox": [1, 2, 3, 4],
                               "source_text": "d 5", "score": 0.9}],
                 "Empty": []}
        reading = citations_reading(cites)
        self.assertEqual(len(reading), 1)
        self.assertEqual(reading[0]["label"], "Invoice No")

    def test_docling_evidence_flips_boxes(self):
        prov = SimpleNamespace(page_no=1, bbox=SimpleNamespace(
            l=32.0, t=770.0, r=215.0, b=736.0))
        doc = SimpleNamespace(
            pages={1: SimpleNamespace(size=SimpleNamespace(width=595.0, height=842.0))},
            texts=[SimpleNamespace(text="Hello", label="text", prov=[prov])],
            tables=[])
        layout, reading = docling_engine.document_evidence(doc)
        self.assertEqual(layout[0]["regions"][0]["bbox"], [32.0, 72.0, 215.0, 106.0])
        self.assertEqual(reading[0]["content"], "Hello")
        self.assertEqual(reading[0]["page"], 1)
        # missing provenance: text kept, no box
        doc2 = SimpleNamespace(pages={}, texts=[SimpleNamespace(text="Hi", prov=[])], tables=[])
        layout2, reading2 = docling_engine.document_evidence(doc2)
        self.assertEqual(layout2, [])
        self.assertEqual(reading2[0]["bbox"], [])

    def test_layout_evidence_normalizes_and_truncates(self):
        pages = [{"page_index": 0, "blocks": [
            {"label": "table", "content": "t" * 2000, "bbox": [150, 300, 450, 600]},
            {"label": "", "content": "hello", "bbox": [0, 0, 150, 150]},
            {"label": "text", "content": "dropped", "bbox": [0, 0, 10]},
        ]}]
        layout, reading = ppdoclayout_engine.layout_evidence(pages, is_pdf=True)
        self.assertEqual(len(layout), 1)
        self.assertEqual(layout[0]["page"], 1)
        # 150dpi worker pixels -> print points (x72/150)
        self.assertEqual(layout[0]["regions"][0]["bbox"], [72.0, 144.0, 216.0, 288.0])
        self.assertEqual(layout[0]["regions"][1]["label"], "text")
        self.assertEqual(len(reading), 2)
        self.assertTrue(reading[0]["content"].endswith("…"))
        self.assertLessEqual(len(reading[0]["content"]), 1501)
        layout_img, _ = ppdoclayout_engine.layout_evidence(pages, is_pdf=False)
        self.assertEqual(layout_img[0]["regions"][0]["bbox"], [150, 300, 450, 600])


if __name__ == "__main__":
    unittest.main()
