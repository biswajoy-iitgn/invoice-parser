#!/usr/bin/env python3
"""Dual-path SR verification: extract critical fields from LR and SR, compare.

For low-resolution documents the pipeline keeps both the original LR image
and the SR image. Critical fields are extracted from both; any disagreement
routes the invoice to REVIEW instead of silently trusting super-resolved
output (SR can render visually plausible but wrong characters).

Usage:
  OMNISR_REF=/tmp/omni-sr-ref \\
  .venv/bin/python scripts/dual_verify.py --split val --count 10 --output work/dual.json
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.extract import extract_invoice  # noqa: E402
from scripts.evaluate import field_equal  # noqa: E402
from scripts.run_eval import prepare_image  # noqa: E402

CRITICAL = ["Invoice No", "Invoice Date", "Vendor Name", "Container Number",
            "MAWB No", "HAWB No", "Bill of Lading / HBL", "WEIGHT"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", default="qwen3-vl:8b-instruct")
    a = ap.parse_args()
    root = Path(a.root)
    rows = [json.loads(x) for x in open(root / f"{a.split}.jsonl") if x.strip()][:a.count]
    out = []
    with tempfile.TemporaryDirectory(prefix="dual-verify-") as td:
        for rec in rows:
            lr_img = prepare_image(rec, root, "lr", Path(td))
            sr_img = prepare_image(rec, root, "srx2", Path(td))
            f_lr, _ = extract_invoice(lr_img, model=a.model)
            f_sr, _ = extract_invoice(sr_img, model=a.model)
            disagreements = [k for k in CRITICAL
                             if not field_equal(k, f_lr.get(k), f_sr.get(k))]
            out.append({"id": rec["id"],
                        "route": "REVIEW" if disagreements else "AUTO_PATH_OK",
                        "sr_original_disagreements": disagreements,
                        "lr_critical": {k: f_lr.get(k) for k in CRITICAL},
                        "sr_critical": {k: f_sr.get(k) for k in CRITICAL}})
            print(f"{rec['id']}: {out[-1]['route']} {disagreements}", flush=True)
    Path(a.output).write_text(json.dumps(out, indent=2))
    n = sum(1 for r in out if r["route"] == "REVIEW")
    print(f"{n}/{len(out)} routed to REVIEW on SR/original disagreement")


if __name__ == "__main__":
    main()
