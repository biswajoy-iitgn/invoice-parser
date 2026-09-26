import json
import re
from pathlib import Path

import jsonschema
import pytest

from invoice_extractor import extract as extract_mod
from invoice_extractor import llm as llm_mod
from invoice_extractor.evaluate import grounded, match, schema_check, score_docs
from invoice_extractor.schema import CHARGE_CATEGORIES, OUTPUT_FIELDS, OUTPUT_JSON_SCHEMA, finalize
from invoice_extractor.semantic import HEADER_LABELS, classify_charge, find_labels
from invoice_extractor.validators import (clean_id, find_containers, normalize_mawb, parse_date,
                                          parse_number)

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("text,value", [
    ("1,66,069.75", 166069.75), ("166,069.75", 166069.75), ("1,583.740", 1583.74), ("10,230", 10230.0),
    ("3,800,00", 3800.0), ("(1,200.00)", -1200.0), ("Rs. 5,500.00", 5500.0), ("18%", 18.0), ("abc", None),
])
def test_parse_number(text, value):
    assert parse_number(text) == value


@pytest.mark.parametrize("text,iso", [
    ("13-May-2024", "2024-05-13"), ("13th May 2024", "2024-05-13"), ("May 13, 2024", "2024-05-13"),
    ("13/05/24", "2024-05-13"), ("13.05.2024", "2024-05-13"), ("12-MAR-25", "2025-03-12"), ("Marine 2024", None),
])
def test_parse_date_day_first(text, iso):
    assert parse_date(text) == iso


def test_check_digits():
    assert find_containers("Cntr BMOU 314916 0 / 20GP, HLXU8364248; bad HLXU8364249") == ["BMOU3149160", "HLXU8364248"]
    assert normalize_mawb("057 5692 7334") == "057-56927334"
    assert normalize_mawb("99140782651") is None            # bank a/c: fails the mod-7 check
    assert clean_id("3,050.00") is None and clean_id("13/05/2024") is None
    assert clean_id("CLL/HBL/24-25/4330") == "CLL/HBL/24-25/4330"


def test_label_negative_contexts():
    assert [h.field for h in find_labels("Invoice No")] == ["invoice_no"]
    assert find_labels("Party Invoice No") == []
    assert find_labels("S/BILL NO") == []
    assert find_labels("Due Date") == [] and find_labels("Chargeable Weight") == []
    assert find_labels("HBL2285054") == []                   # a value that starts with a label word
    assert [h.field for h in find_labels("Weight: 20,209.553 KGS")] == ["gross_weight"]


@pytest.mark.parametrize("desc,mode,cat", [
    ("THC - Origin", "sea", "Terminal Handling Charges"),
    ("Terminal Handling (Origin)", "sea", "Terminal Handling Charges"),
    ("Documentation Fee (B/L)", "sea", "BL Charges"),
    ("Road Transport (Factory to CFS)", "sea", "Transportation"),
    ("Emergency Bunker Surcharge (EBS)", "sea", "Fuel Charges"),
    ("Freight Charges - Air", "air", "Air Freight"),
    ("Air Waybill Fee", "air", "Airway Bill Charges"),
    ("TERMINALHANDLINGCHARGES", "sea", "Terminal Handling Charges"),
    ("Termlnal Handllng Charges", "sea", "Terminal Handling Charges"),
    ("Container Cleaning Charges", "sea", "Other Charges"),
])
def test_classify_charge(desc, mode, cat):
    assert classify_charge(desc, mode)[0] == cat


def _heldout_strings(start: str, end: str) -> list[str]:
    html = (ROOT / "invoice-maker.html").read_text()
    block = html[html.index(start):html.index(end)]
    return [s for arr in re.findall(r"ho:\[([^\]]*)\]", block) for s in re.findall(r'"([^"]+)"', arr)]


def _literal(pattern: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\\[sbwdS]\*?\??|\(\?[^)]*\)|[\\\[\]()?*+^$|{}]", " ", pattern)).lower().strip()


def _leaks(heldout: list[str], patterns: list[str]) -> list[str]:
    literals = [_literal(p) for p in patterns]
    return [h for h in heldout if any(h.lower() in lit for lit in literals)]


def test_no_heldout_wording_in_extractor_lexicon():
    """Held-out wordings/labels measure generalisation, so none may appear verbatim in the extractor's rules."""
    charges = _heldout_strings("const LOG_CHARGES=", "const LOG_LAB=")
    labels = _heldout_strings("const LOG_LAB=", "const LOG_SECT=")
    assert len(charges) > 40 and len(labels) > 15
    assert _leaks(charges, [p for spec in CHARGE_CATEGORIES.values() for p in spec["patterns"]]) == []
    assert _leaks(labels, [p for pats in HEADER_LABELS.values() for p in pats]) == []


def test_finalize_contract():
    out = finalize({"Invoice No": " INV-1 ", "Sea Freight": "1,234.5", "Weight": 530.35, "Ex Rate": 86.27,
                    "Unexpected": "x"})
    assert list(out) == OUTPUT_FIELDS
    jsonschema.validate(out, OUTPUT_JSON_SCHEMA)
    assert out["Invoice No"] == "INV-1" and out["Sea Freight"] == 1234.5 and out["Weight"] == "530.35 KG"
    assert out["Air Freight"] == "" and schema_check(out) == []
    assert "extra_keys" in schema_check({**out, "Total": 1}) and "non_standard_empty_marker" in schema_check({**out, "MAWB No": "N/A"})


def test_metrics_definitions():
    gold = {f: "" for f in OUTPUT_FIELDS}
    gold.update({"Invoice No": "INV-1", "Sea Freight": 1000.0, "BL Charges": 500.0})
    pred = {f: "" for f in OUTPUT_FIELDS}
    pred.update({"Invoice No": "inv-1", "Sea Freight": 1000.0, "BL Charges": 400.0, "VGM": 99.0})
    text = "Invoice No INV-1  Ocean Freight 1,000.00  BL Fee 500.00  Total 1,500.00"
    s = score_docs([{"id": "x", "gold": gold, "pred": pred, "conf": {}, "gold_text": text, "meta": {}, "lines": [], "audit": {}}])
    assert (s["fields"]["Invoice No"]["tp"], s["fields"]["BL Charges"]["wrong"], s["fields"]["VGM"]["fabricated"]) == (1, 1, 1)
    assert s["micro"]["precision"] == pytest.approx(2 / 4) and s["micro"]["recall"] == pytest.approx(2 / 3)
    assert s["hallucination"]["fabrication_rate"] == pytest.approx(1 / 4)
    assert s["hallucination"]["ungrounded_rate"] == pytest.approx(2 / 4)   # 400 and 99 are printed nowhere
    assert grounded("Sea Freight", 1500.0, text, "", [1000.0, 500.0], set())  # sum of printed amounts
    assert match("Container Number", "ABCU1234567, DEFU7654321", "DEFU7654321,ABCU1234567")


DIGITAL = ROOT / "dataset" / "invoices" / "dev-002.pdf"


@pytest.mark.skipif(not DIGITAL.exists(), reason="dataset not built")
def test_end_to_end_digital_pdf():
    fields, audit = extract_mod.extract_invoice(str(DIGITAL), ocr="tesseract")
    gold = json.loads((ROOT / "dataset" / "ground_truth" / "dev-002.json").read_text())["fields"]
    assert list(fields) == OUTPUT_FIELDS and audit["subtotal_reconciled"] is True
    for f in ("Invoice No", "Invoice Date", "Vendor Name", "MAWB No", "Air Freight", "Fuel Charges", "Ex Rate"):
        assert match(f, gold[f], fields[f]), f


@pytest.mark.skipif(not DIGITAL.exists(), reason="dataset not built")
def test_llm_fallback_only_touches_unplaced_lines(monkeypatch):
    seen = {}

    def fake(descs, mode, model):
        seen["descs"] = descs
        return ["Handling Charges"] * len(descs)

    monkeypatch.setattr(llm_mod, "classify_lines", fake)
    _, audit = extract_mod.extract_invoice(str(DIGITAL), backend="layout+llm", ocr="tesseract")
    llm_lines = [it for it in audit["line_items"] if it["category_basis"] == "llm"]
    assert len(llm_lines) == len(seen["descs"])
    assert all(it["category_basis"] != "default" for it in audit["line_items"])
