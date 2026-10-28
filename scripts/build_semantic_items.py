#!/usr/bin/env python3
"""Semantic-engine training items from the InvoiceLab v2 corpus.

One item = one printed surface form + its context + the canonical class it means:
  kind=label   a key label as printed ("Inv. Dt.", "H B/L No./HAWB No.", "Seal No") with a value sample
  kind=charge  a charge-line description as printed ("OTHER CHARGES THC", "O/F FCL", "Sub Total")
Classes: semantic.CLASSES (9 header targets, 2 auxiliary fallbacks, 22 charge categories, NONE).

Train items come only from train documents (+ curated ontology aliases that are not held out);
val/test items keep their alias bucket so metrics split into seen vs unseen wording.

Usage: .venv/bin/python scripts/build_semantic_items.py dataset_v2 --out work/sem_v2
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_extractor.semantic import CLASSES, context_string, normalize  # noqa: E402

CODE_TO_CLASS = {
    "invoice_number": "Invoice No", "invoice_date": "Invoice Date", "container_number": "Container Number",
    "mawb_number": "MAWB No", "hawb_number": "HAWB No", "vendor_name": "Vendor Name",
    "bill_of_lading_hbl": "Bill of Lading / HBL", "weight": "WEIGHT", "exchange_rate": "Ex Rate",
    "master_bl": "Master B/L", "chargeable_weight": "Chargeable Weight",
}
# Rows the reader must never treat as charges (totals, taxes, carry-forward).
NON_CHARGE_ROWS = ["Sub Total", "Net Total", "Total Taxable Value", "Taxable Total", "CGST", "SGST", "IGST",
                   "Add : CGST", "Add : SGST", "CGST9 (9%)", "SGST9 (9%)", "Round Off", "Round-Off", "Grand Total",
                   "TOTAL INVOICE AMOUNT", "Total Invoice Value", "Net Payable", "TOTAL DUE", "B/F", "C/F",
                   "Brought Forward", "Carried Forward", "Less : Advance Received", "TDS", "Amount in words",
                   "Total Amount Before Tax", "Gross Total", "Total Tax", "Tax Payable on Reverse Charge"]
OCR_CONFUSE = {"O": "0", "0": "O", "l": "1", "1": "l", "I": "1", "S": "5", "5": "S", "B": "8", "e": "c", "rn": "m",
               ".": ",", ",": "."}


def ocr_noise(s: str, rnd: random.Random) -> str:
    out = s
    for _ in range(rnd.randint(1, 2)):
        keys = [k for k in OCR_CONFUSE if k in out]
        if not keys:
            break
        k = rnd.choice(keys)
        i = out.find(k)
        out = out[:i] + OCR_CONFUSE[k] + out[i + len(k):]
    if rnd.random() < 0.3:
        out = out.replace(" ", "", 1)
    return out


def doc_items(row: dict, rnd: random.Random) -> list[dict]:
    split, items = row["split"], []
    ctx = {"mode": row["mode"], "region": row.get("region") or "", "incoterm": row.get("incoterm") or ""}
    for code, lab in row["labels"].items():
        if not lab:
            continue
        cls = CODE_TO_CLASS.get(code, "NONE")
        val = row["values"].get(code) or ""
        items.append({"kind": "label", "text": lab["text"], "alias": lab["raw"], "field": cls,
                      "bucket": lab["bucket"], "context": context_string("label", value=str(val)[:28], **ctx),
                      "code": code, "doc_id": row["id"], "split": split})
    for it in row["items"]:
        text = " ".join(x for x in (it["descMain"], it["descSub"]) if x)
        items.append({"kind": "charge", "text": text, "alias": it["alias"], "field": it["category"],
                      "bucket": it["alias_bucket"], "context": context_string("charge", **ctx),
                      "code": "charge", "doc_id": row["id"], "split": split})
    for t in rnd.sample(NON_CHARGE_ROWS, 2):
        items.append({"kind": "charge", "text": t, "alias": t, "field": "NONE", "bucket": "train",
                      "context": context_string("charge", **ctx), "code": "total_row", "doc_id": row["id"],
                      "split": split})
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default="work/sem_v2")
    ap.add_argument("--noise", type=float, default=0.3, help="share of train items duplicated with OCR noise")
    a = ap.parse_args()
    root, out = Path(a.root), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(91731)
    held_out = set()
    splits = {}
    for split in ("train", "val", "test"):
        rows = [json.loads(l) for l in (root / f"{split}.jsonl").open()]
        its = [x for r in rows for x in doc_items(r, rnd)]
        if split != "train":
            held_out |= {normalize(x["alias"]) for x in its if x["bucket"] in ("val", "test")}
        splits[split] = its
    # curated ontology aliases (train only, never a held-out surface form)
    onto = json.loads((ROOT / "configs" / "field_ontology.json").read_text())
    extra = 0
    for field, spec in onto.items():
        for alias in [field, *spec.get("aliases", []), *spec.get("abbreviations", [])]:
            if normalize(alias) in held_out or field not in CLASSES:
                continue
            kind = "label" if field in ("Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No",
                                        "Vendor Name", "Bill of Lading / HBL", "WEIGHT", "Ex Rate") else "charge"
            for mode in ("sea", "air"):
                splits["train"].append({"kind": kind, "text": alias, "alias": alias, "field": field, "bucket": "train",
                                        "context": context_string(kind, mode=mode, region="", incoterm=""),
                                        "code": "ontology", "doc_id": "ontology", "split": "train"})
                extra += 1
        for neg in spec.get("negative_examples", []):
            if normalize(neg) in held_out:
                continue
            kind = "label" if any(w in neg.lower() for w in ("no", "number", "date", "weight")) else "charge"
            splits["train"].append({"kind": kind, "text": neg, "alias": neg, "field": "NONE", "bucket": "train",
                                    "context": context_string(kind, mode="sea", region="", incoterm=""),
                                    "code": "ontology_neg", "doc_id": "ontology", "split": "train"})
    noisy = [dict(x, text=ocr_noise(x["text"], rnd), code=x["code"] + "+ocr")
             for x in splits["train"] if rnd.random() < a.noise]
    splits["train"] += noisy
    rnd.shuffle(splits["train"])
    stats = {}
    for split, its in splits.items():
        with (out / f"{split}.jsonl").open("w") as f:
            for x in its:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        stats[split] = {"items": len(its), "unseen": sum(x["bucket"] in ("val", "test") for x in its),
                        "classes": len({x["field"] for x in its})}
    stats["ontology_items"] = extra
    stats["ocr_noised_train_items"] = len(noisy)
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
