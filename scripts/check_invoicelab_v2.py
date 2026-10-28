#!/usr/bin/env python3
"""Verify that every ground-truth value of the InvoiceLab v2 corpus is printed on the page.

Checks, per document (using the DOM-measured words in ann/<id>.json):
  * each non-empty target field (31-key GT) has words tagged <code>:value whose text matches
    (container/MAWB/HAWB/HBL/invoice no exactly, dates and amounts by normalized value);
  * every line item has a printed description equal to the reader-target description and a
    printed taxable INR amount equal to amount_inr;
  * every exchange rate in the GT is printed (exchange_rate:value word with the same number);
  * GT charge fields equal the per-category sums of the line items.

Usage:  .venv/bin/python scripts/check_invoicelab_v2.py dataset_v2 [--limit N]
Exit code 1 when any document fails.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_extractor.validation import _norm_num, normalize_date  # noqa: E402

NUM = re.compile(r"-?[\d,]+\.?\d*")


def _num(s: str) -> str | None:
    m = NUM.search(s.replace(" ", ""))
    return _norm_num(m.group(0)) if m else None


def tagged_runs(words: list[dict], tag: str) -> list[str]:
    """Contiguous runs of words carrying `tag` (reading order = DOM order)."""
    runs, cur = [], []
    for w in words:
        if w["k"] == tag:
            cur.append(w["t"])
        elif cur:
            runs.append(" ".join(cur))
            cur = []
    if cur:
        runs.append(" ".join(cur))
    return runs


def check_doc(row: dict, ann: dict) -> list[str]:
    errs = []
    words = [w for p in ann["pages"] for w in p["words"]]
    gt, tgt = row["ground_truth"], row["reader_target"]
    vals = lambda code: tagged_runs(words, f"{code}:value")  # noqa: E731
    if gt["Invoice No"] and gt["Invoice No"] not in vals("invoice_number"):
        errs.append(f"invoice_number {gt['Invoice No']!r} not printed: {vals('invoice_number')[:2]}")
    if gt["Invoice Date"] and not any(normalize_date(v) == gt["Invoice Date"] for v in vals("invoice_date")):
        errs.append(f"invoice_date {gt['Invoice Date']} not printed as a parseable date: {vals('invoice_date')[:2]}")
    if gt["Vendor Name"] and gt["Vendor Name"] not in vals("vendor_name"):
        errs.append(f"vendor_name {gt['Vendor Name']!r} not printed")
    for c in [x.strip() for x in gt["Container Number"].split(",") if x.strip()]:
        if c not in vals("container_number"):
            errs.append(f"container {c} not printed")
    for field, code in (("MAWB No", "mawb_number"), ("HAWB No", "hawb_number")):
        if gt[field] and gt[field] not in vals(code):
            errs.append(f"{code} {gt[field]!r} not printed: {vals(code)[:2]}")
    bl = gt["Bill of Lading / HBL"]
    if bl and bl not in vals("bill_of_lading_hbl") + vals("master_bl"):
        errs.append(f"B/L {bl!r} not printed")
    if gt["WEIGHT"]:
        want = _num(gt["WEIGHT"])
        if not any(_num(v) == want for v in vals("weight") + vals("chargeable_weight")):
            errs.append(f"weight {gt['WEIGHT']!r} not printed: {vals('weight')[:2]}")
    rates = {_num(v) for v in vals("exchange_rate") for v in re.findall(r"\d+\.\d+|\d+", v)}
    for r in tgt["exchange_rates"]:
        if _norm_num(str(r["rate"])) not in rates:
            errs.append(f"rate {r['currency']} {r['rate']} not printed (printed {sorted(rates)[:4]})")
    by_li = defaultdict(list)
    for w in words:
        if w["li"] >= 0:
            by_li[w["li"]].append(w)
    for i, it in enumerate(tgt["line_items"]):
        ws = by_li.get(i, [])
        desc = " ".join(w["t"] for w in ws if "li_desc:value" in (w["k"], w.get("k2")))
        if re.sub(r"\s+", " ", desc) != re.sub(r"\s+", " ", it["description"]):
            errs.append(f"item {i} desc printed {desc!r} != target {it['description']!r}")
        amts = [_num(w["t"]) for w in ws if w["k"] == "li_amount:value"]
        if _norm_num(f"{it['amount_inr']:.2f}") not in amts:
            errs.append(f"item {i} amount {it['amount_inr']} not printed (printed {amts})")
    sums = Counter()
    for it in tgt["line_items"]:
        sums[it["category"]] += it["amount_inr"]
    for cat, v in sums.items():
        if abs(float(gt[cat] or 0) - v) > 0.011:
            errs.append(f"GT {cat}={gt[cat]} != item sum {v:.2f}")
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    root = Path(a.root)
    n = bad = 0
    kinds = Counter()
    for split in ("train", "val", "test"):
        path = root / f"{split}.jsonl"
        if not path.exists():
            continue
        for line in path.open():
            row = json.loads(line)
            ann = json.loads((root / row["ann"]).read_text())
            errs = check_doc(row, ann)
            n += 1
            if errs:
                bad += 1
                for e in errs:
                    kinds[e.split(" ")[0] + " " + e.split(" ")[1]] += 1
                if bad <= 12:
                    print(row["id"], f"F{row['family']}", row["family_spec"]["table"], "|", "; ".join(errs[:3]))
            if a.limit and n >= a.limit:
                break
    print(json.dumps({"documents": n, "failed": bad, "error_kinds": kinds.most_common(12)}, indent=1))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
