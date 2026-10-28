#!/usr/bin/env python3
"""Build reader-LLM fine-tune pairs from the invoice corpus (MLX format).

Input  = document text reconstructed from labeled regions in reading order
         (approximates HunyuanOCR page text; vendor name injected as the
         rendered header line since it has no labeled region).
Target = extraction JSON in the extract.py LLM schema (line items with
         ground-truth categories from region labels).

Output: one JSONL per split with {"text": "<prompt><completion>"} rows for
`python -m mlx_lm.lora`. Held-out decoys/annotations are kept in the input
(they are printed on the page) so the reader learns to ignore them.

Usage:
  .venv/bin/python scripts/build_reader_items.py --split train --output work/reader_train.jsonl
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.schema import CHARGE_FIELDS  # noqa: E402

AMOUNT_RE = re.compile(r"([A-Z]{3}|Rs\.?|INR|USD|EUR|GBP|AED)\s*([\d,]+\.\d{2})")
RATE_RE = re.compile(r"([A-Z]{3})/INR\s*([\d.]+)")


def _strip_label(text: str) -> str:
    return re.sub(r"^[^:]{1,40}:\s*", "", text.strip())


def _parse_amount(text: str) -> tuple[str, float]:
    m = None
    for m in AMOUNT_RE.finditer(text):
        pass
    if not m:
        return "INR", 0.0
    cur = m.group(1)
    cur = {"Rs": "INR", "Rs.": "INR"}.get(cur, cur)
    return cur, float(m.group(2).replace(",", ""))


def build_target(rec: dict) -> tuple[str, dict] | tuple[None, None]:
    """Returns (doc_text, target_json). Target None if no line items."""
    regions = sorted(rec.get("regions", []), key=lambda b: (b["bbox"][1], b["bbox"][0]))
    gt = rec.get("ground_truth", {})
    texts = [b["text"] for b in regions]
    vendor = gt.get("Vendor Name", "")
    if vendor:
        texts.insert(0, vendor)  # rendered header, no labeled region
    doc = "===== PAGE 1 =====\n" + "\n".join(texts)

    by_field: dict[str, list[str]] = {}
    for b in regions:
        by_field.setdefault(b["field"], []).append(b["text"])
    first = lambda f: _strip_label(by_field[f][0]) if f in by_field else ""

    rates = []
    for t in by_field.get("Ex Rate", []):
        for m in RATE_RE.finditer(t):
            if m.group(1) != "INR":
                rates.append({"currency": m.group(1), "rate": float(m.group(2))})
    rate_of = {r["currency"]: r["rate"] for r in rates}

    items = []
    for b in regions:
        if b["field"] not in CHARGE_FIELDS:
            continue
        cur, num = _parse_amount(b["text"])
        if cur != "INR":
            if cur not in rate_of:
                continue
            num = round(num * rate_of[cur], 2)
        items.append({"description": b["text"], "amount_inr": num, "category": b["field"]})
    if not items:
        return None, None

    has_awb = bool(by_field.get("MAWB No") or by_field.get("HAWB No"))
    target = {
        "invoice_no": first("Invoice No"),
        "invoice_date": first("Invoice Date"),
        "vendor_name": vendor,
        "transport_mode": "air" if has_awb else "sea",
        "destination_country": "",
        "container_numbers": [c for t in by_field.get("Container Number", [])
                              for c in re.findall(r"[A-Z]{4}\d{7}", t)],
        "mawb_no": first("MAWB No"),
        "hawb_no": first("HAWB No"),
        "master_bl_no": "",
        "house_bl_no": first("Bill of Lading / HBL"),
        "gross_weight": first("WEIGHT"),
        "chargeable_weight": "",
        "exchange_rates": rates,
        "line_items": items,
        "subtotal_before_tax": None,
        "total_invoice_amount": None,
    }
    return doc, target


VL_QUESTION = ("Extract this freight invoice to JSON ONLY (no prose). "
               "Copy numbers verbatim as printed. Missing field -> null or []. "
               "Keys: invoice_no, invoice_date, vendor_name, transport_mode, "
               "destination_country, container_numbers, mawb_no, hawb_no, "
               "master_bl_no, house_bl_no, gross_weight, chargeable_weight, "
               "exchange_rates, line_items (array of {description, amount_inr, "
               "category}), subtotal_before_tax, total_invoice_amount.")


def build_pair(rec: dict) -> dict | None:
    doc, target = build_target(rec)
    if target is None or not target["line_items"]:
        return None
    prompt = ("You read the raw text of one invoice and return a JSON object.\n\n"
              f"INVOICE TEXT:\n\n{doc}\n\nReturn the JSON now.")
    return {"text": prompt + "\n" + json.dumps(target, ensure_ascii=False)}


def build_vl_pair(rec: dict, image_root: str) -> dict | None:
    doc, target = build_target(rec)
    if target is None or not target["line_items"]:
        return None
    return {"image": f"{image_root}/{rec['image']}",
            "question": VL_QUESTION,
            "answer": json.dumps(target, ensure_ascii=False)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--output", required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    n, skipped = 0, 0
    with open(a.output, "w") as f:
        for line in open(Path(a.root) / f"{a.split}.jsonl"):
            if a.limit and n >= a.limit:
                break
            pair = build_pair(json.loads(line))
            if pair is None:
                skipped += 1
                continue
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n} pairs -> {a.output} (skipped {skipped})")


if __name__ == "__main__":
    main()
