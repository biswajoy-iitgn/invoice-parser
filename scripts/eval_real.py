#!/usr/bin/env python3
"""Score the full pipeline on the held-out real invoices (eval/real_invoices/gold.json).

The real set is never used for training or tuning; this is the out-of-distribution
check that tells whether synthetic training generalizes.

Usage:
  .venv/bin/python scripts/eval_real.py --tag baseline_old
  READER_BACKEND=mlx .venv/bin/python scripts/eval_real.py --tag reader_v2
  .venv/bin/python scripts/eval_real.py --tag baseline_old --rescore   # no re-extraction
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.scoring import aggregate, score_document, score_line_items  # noqa: E402

GOLD = ROOT / "eval" / "real_invoices" / "gold.json"
SAMPLES = ROOT / "sample invoices"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="run name; predictions cached under work/real_eval/<tag>/")
    ap.add_argument("--rescore", action="store_true", help="score cached predictions only")
    ap.add_argument("--only", default="", help="comma-separated invoice ids")
    ap.add_argument("--scanned", default="", help="comma-separated scan recipes (office,phone): use the "
                    "image-only scanned copies from scripts/make_real_scans.py instead of the digital PDFs")
    a = ap.parse_args()
    gold = json.loads(GOLD.read_text())
    out_dir = ROOT / "work" / "real_eval" / a.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    only = {x for x in a.only.split(",") if x}
    docs, report_docs = [], []
    variants = [(inv, r) for inv in gold["invoices"] for r in (a.scanned.split(",") if a.scanned else [""])]
    for inv, recipe in variants:
        if only and inv["id"] not in only:
            continue
        tag = inv["id"] + (f"__{recipe}" if recipe else "")
        cache = out_dir / f"{tag}.json"
        if not a.rescore or not cache.exists():
            from invoice_extractor.extract import DEFAULT_MODEL, extract_invoice

            path = (ROOT / "eval" / "real_invoices" / "scanned" / f"{tag}.pdf") if recipe else SAMPLES / inv["files"][0]
            t0 = time.time()
            try:
                fields, audit = extract_invoice(str(path), model=os.environ.get("INVOICE_MODEL", DEFAULT_MODEL))
                pred = {"fields": fields, "line_items": audit.get("line_items", []),
                        "warnings": audit.get("warnings", []), "timing_sec": audit.get("timing_sec", {}),
                        "semantic": audit.get("semantic", {}), "elapsed_s": round(time.time() - t0, 2)}
            except Exception as exc:  # keep scoring the rest
                pred = {"fields": {}, "line_items": [], "error": f"{type(exc).__name__}: {exc}"[:400],
                        "elapsed_s": round(time.time() - t0, 2)}
            cache.write_text(json.dumps(pred, indent=2, ensure_ascii=False, default=str))
            print(f"{tag}: {pred.get('elapsed_s')}s {pred.get('error', '')}", flush=True)
        pred = json.loads(cache.read_text())
        fs = score_document(inv["fields"], pred.get("fields", {}), inv.get("alternatives"))
        it = score_line_items(inv["line_items"], pred.get("line_items", []))
        docs.append({"fields": fs, "items": it})
        report_docs.append({
            "id": tag,
            "field_errors": {k: {"gold": v["gold"], "pred": v["pred"]}
                             for k, v in fs.items() if not v["lenient"]},
            "line_items": it,
            "error": pred.get("error", ""),
        })
    summary = aggregate(docs)
    report = {"tag": a.tag, "env": {k: os.environ.get(k, "") for k in
                                    ("READER_BACKEND", "INVOICE_MODEL", "INVOICE_INGEST",
                                     "SEMANTIC_ENGINE", "MLX_READER_MODEL", "MLX_READER_ADAPTER")},
              "summary": summary, "documents": report_docs}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    s, l = summary["strict"], summary["lenient"]
    print(json.dumps({
        "tag": a.tag, "documents": summary["documents"],
        "critical_strict": round(s["critical_field_accuracy"], 3),
        "critical_lenient": round(l["critical_field_accuracy"], 3),
        "charges_strict": round(s["charge_field_accuracy"], 3),
        "charges_lenient": round(l["charge_field_accuracy"], 3),
        "all_fields_lenient": round(l["field_accuracy"], 3),
        "invoice_exact_lenient": round(l["invoice_exact_match"], 3),
        "line_items": summary.get("line_items"),
    }, indent=2))


if __name__ == "__main__":
    main()
