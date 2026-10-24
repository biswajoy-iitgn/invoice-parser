#!/usr/bin/env python3
"""For every output key that came back blank, look on the document for information that could belong to it.

  LAYA_DEVICE=cpu .venv/bin/python scripts/audit_blank_fields.py            # the cached real invoices (digital + scans)

Header keys: labels and patterns that usually carry the value (container codes, air waybill numbers, B/L labels,
weights with a unit, exchange rates). Charges: whether the charge lines add up to the printed subtotal, and any
line that was left out. Prints only the suspicious cases.
"""
from __future__ import annotations

import copy
import glob
import json
import pickle
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CLUES = {
    "Container Number": (r"container|cntr|equipment|\bcont\.?\s?no", r"\b[A-Z]{3}[UJZ]\s?\d{6}\s?\d\b"),
    "MAWB No": (r"\bM\.?\s?A\.?\s?W\.?\s?B\b|master\s+air|\bAWB\b|air\s?way\s?bill", r"(?<!\d)\d{3}[- ]\d{4}[- ]?\d{4}(?!\d)"),
    "HAWB No": (r"\bH\.?\s?A\.?\s?W\.?\s?B\b|house\s+air", None),
    "Bill of Lading / HBL": (r"\bB\s?/\s?L\b|\bBL\s?(no|number|#)|bill\s+of\s+lading|\bH\.?B\.?L\b|\bM\.?B\.?L\b|\bOBL\b|sea\s?way\s?bill|\bSWB\b", None),
    "WEIGHT": (r"weight|\bwt\b|\bgr\.?\s?wt|gross|chargeable|\bG\.?W\b|\bC\.?W\b|\bN\.?W\b", r"\b\d[\d,]*\.?\d*\s?(KGS?|KG\.|kilograms?|MT|LBS?)\b"),
    "Ex Rate": (r"ex(change)?\.?\s?rate|\bROE\b|rate\s+of\s+exchange|\bconv(ersion)?\.?\s?rate", r"(USD|EUR|GBP|AED|SGD|JPY|CNY)\s?[\d,]+\.?\d*\s?@\s?\d+\.?\d*|@\s?\d{2,3}\.\d{2,6}"),
    "Invoice No": (r"invoice\s?(no|number|#)|inv\.?\s?no|bill\s?no", None),
    "Invoice Date": (r"invoice\s?date|inv\.?\s?(dt|date)|\bdate\b|dated", None),
    "Vendor Name": (r"^for\s|pvt|private|limited|ltd|llp", None),
}


def main() -> None:
    import fusion_study as fs
    from invoice_extractor.kv import candidates
    from invoice_extractor.pipeline import assemble
    from invoice_extractor.schema import CHARGE_FIELDS

    fs.memoize_models()
    files = sys.argv[1:] or sorted(glob.glob(str(ROOT / "work/fusion_cache/real_digital/*.pkl")) +
                                   glob.glob(str(ROOT / "work/fusion_cache/real_scan/*.pkl")))
    clean = 0
    for f in files:
        c = pickle.loads(open(f, "rb").read())
        fields, audit = assemble(copy.deepcopy(c["raw"]), c["text"], c["index"], c["pages"])
        notes = []
        kvs = candidates(c["index"])
        facts = [x for x in audit.get("facts", []) if x["kind"] in ("pair", "table", "block") and x["label"] != "(column title)"]
        for key, (label_rx, value_rx) in CLUES.items():
            if fields.get(key):
                continue
            seen = []
            for k in kvs:
                if re.search(label_rx, k.label, re.I) and re.search(r"\d|[A-Za-z]{3}", k.value):
                    seen.append(f"label '{k.label}' -> '{k.value[:40]}'")
            for x in facts:
                if re.search(label_rx, x["label"], re.I) and not any(x["value"][:20] in s for s in seen):
                    seen.append(f"label '{x['label']}' -> '{x['value'][:40]}' (p{x['page']})")
            if value_rx:
                for m in re.finditer(value_rx, c["text"]):
                    if not any(m.group(0) in s for s in seen):
                        seen.append(f"pattern '{m.group(0)}'")
            if seen:
                notes.append(f"   {key} is blank, but the document has: " + "; ".join(dict.fromkeys(seen))[:420])
        left_out = [i for i in audit.get("line_items", []) if i.get("excluded")]
        for i in left_out:
            notes.append(f"   charge line left out ({i['excluded']}): '{i['description'][:50]}' {i['amount_inr']:,.2f}")
        if audit.get("subtotal_reconciled") is False:
            notes.append(f"   charge lines add up to {audit['line_items_sum_inr']:,.2f}, the printed subtotal is "
                         f"{audit['subtotal_before_tax']:,.2f}: a line may be missing")
        elif audit.get("subtotal_reconciled") is None:
            notes.append("   no printed subtotal could be confirmed, so the completeness of the charge lines is unchecked")
        filled = sum(1 for v in fields.values() if v)
        charges = sum(1 for k in CHARGE_FIELDS if fields.get(k))
        print(f"{c['id']}: {filled} keys filled ({charges} charge categories), route {audit['review_route']}"
              + ("" if notes else " - nothing printed that a blank key could hold"))
        clean += not notes
        for n in notes:
            print(n)
    print(f"{clean} of {len(files)} documents with no finding")


if __name__ == "__main__":
    main()
