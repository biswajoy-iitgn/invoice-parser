#!/usr/bin/env python3
"""Field/document metrics with format-aware comparison.

Currency values compare by normalized number ("USD 13,508.79" == "13508.79");
dates compare by ISO-normalized date ("06/10/2024" == "2024-10-06"). All other
fields compare case-insensitively after whitespace normalization.

Usage:
  .venv/bin/python scripts/evaluate.py dataset/test.jsonl predictions.jsonl --output evaluation.json

Predictions JSONL rows: {"id": ..., "ground_truth": {field: value}, "evidence": "..."}.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_extractor.schema import CHARGE_FIELDS, OUTPUT_FIELDS  # noqa: E402
from invoice_extractor.validation import (  # noqa: E402
    _norm_num,
    normalize_date,
    supported,
    supported_date,
    supported_number,
    supported_rate,
)

FIELDS = OUTPUT_FIELDS
CRITICAL = {"Invoice No", "Invoice Date", "Container Number", "MAWB No", "HAWB No",
            "Vendor Name", "Bill of Lading / HBL", "WEIGHT"}
CURRENCY_FIELDS = set(CHARGE_FIELDS)
NUMBER_RE = re.compile(r"-?[\d,]+\.\d+|-?\d[\d,]*")


def norm(x):
    return re.sub(r"\s+", " ", str(x or "")).strip().casefold()


def field_equal(field: str, gold, pred) -> bool:
    g, p = norm(gold), norm(pred)
    if not g and not p:
        return True
    if not g or not p:
        return False
    if field == "Invoice Date":
        return normalize_date(g) == normalize_date(p)
    if field in CURRENCY_FIELDS:
        gm, pm = NUMBER_RE.search(g), NUMBER_RE.search(p)
        if not gm or not pm:
            return g == p
        return _norm_num(gm.group(0)) == _norm_num(pm.group(0))
    if field == "Ex Rate":
        return _rate_set(g) == _rate_set(p)
    return g == p


def _rate_set(s: str) -> set:
    out = set()
    for entry in s.split(","):
        entry = entry.strip()
        m = NUMBER_RE.search(entry)
        code = entry.split()[0] if entry.split() else ""
        out.add((code, _norm_num(m.group(0)) if m else ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ground_truth")
    ap.add_argument("predictions")
    ap.add_argument("--output", default="evaluation.json")
    a = ap.parse_args()
    gt = {json.loads(l)["id"]: json.loads(l) for l in open(a.ground_truth) if l.strip()}
    pr = {json.loads(l)["id"]: json.loads(l) for l in open(a.predictions) if l.strip()}
    rows = []
    field = {k: {"correct": 0, "n": 0, "tp": 0, "fp": 0, "fn": 0,
                 "unsupported": 0, "pred_nonempty": 0} for k in FIELDS}
    hall = pred_nonempty = gold_nonempty = missing = 0
    missing_ids = sorted(i for i in gt if i not in pr)
    if missing_ids:
        # Missing predictions score zero (exact_document False) instead of
        # being skipped, which would inflate coverage metrics.
        print(f"WARNING: {len(missing_ids)}/{len(gt)} ground-truth ids missing "
              f"from predictions, counted as zero-score: {missing_ids[:10]}",
              file=sys.stderr)
    for i, g in gt.items():
        if i not in pr:
            rows.append({"id": i, "exact_document": False,
                         "split": g.get("split"), "template": g.get("template_id"),
                         "missing_prediction": True})
            continue
        gold = g.get("ground_truth", {})
        pred = pr[i].get("ground_truth", pr[i])
        ev = norm(pr[i].get("evidence", ""))
        inv = 0
        for k in FIELDS:
            y, x = norm(gold.get(k)), norm(pred.get(k))
            m = field[k]
            m["n"] += 1
            eq = field_equal(k, gold.get(k), pred.get(k))
            m["correct"] += eq
            m["tp"] += bool(y and x and eq)
            m["fp"] += bool(x and (not y or not eq))
            m["fn"] += bool(y and not eq)
            gold_nonempty += bool(y)
            missing += bool(y and not x)
            if x:
                pred_nonempty += 1
                m["pred_nonempty"] += 1
                raw_ev = pr[i].get("evidence", "")
                if k == "Invoice Date":
                    ok = supported_date(str(pred.get(k)), raw_ev)
                elif k in CURRENCY_FIELDS:
                    ok = supported_number(str(pred.get(k)), raw_ev)
                elif k == "Ex Rate":
                    ok = supported_rate(str(pred.get(k)), raw_ev)
                else:
                    ok = supported(str(pred.get(k)), raw_ev)
                hall += not ok
                m["unsupported"] += not ok
            inv += eq
        rows.append({"id": i, "exact_document": inv == len(FIELDS),
                     "split": g.get("split"), "template": g.get("template_id")})
    metrics = {}
    for k, m in field.items():
        den, recden = m["tp"] + m["fp"], m["tp"] + m["fn"]
        metrics[k] = {
            "exact_match": m["correct"] / m["n"] if m["n"] else 0,
            "precision": m["tp"] / den if den else 0,
            "recall": m["tp"] / recden if recden else 0,
            "f1": 2 * m["tp"] / (2 * m["tp"] + m["fp"] + m["fn"])
            if 2 * m["tp"] + m["fp"] + m["fn"] else 0,
            "unsupported": m["unsupported"],
            "unsupported_fill_rate": m["unsupported"] / m["pred_nonempty"]
            if m["pred_nonempty"] else 0,
        }
    result = {
        "matched_documents": len(gt) - len(missing_ids),
        "total_documents": len(gt),
        "missing_documents": len(missing_ids),
        "missing_prediction_ids": missing_ids,
        "schema_compliance": sum(
            all(k in pr[i].get("ground_truth", pr[i]) for k in FIELDS)
            for i in gt if i in pr) / max(1, len(gt)),
        "invoice_exact_match": sum(x["exact_document"] for x in rows) / max(1, len(gt)),
        "critical_field_exact_match": sum(
            metrics[k]["exact_match"] for k in CRITICAL) / len(CRITICAL),
        "field_metrics": metrics,
        "hallucination_rate": hall / max(1, pred_nonempty),
        "unsupported_fill_rate": hall / max(1, pred_nonempty),
        "unsupported_predictions": hall,
        "nonempty_predictions": pred_nonempty,
        "missing_field_rate": missing / max(1, gold_nonempty),
        "missing_predictions": missing,
        "nonempty_gold": gold_nonempty,
        "evidence_coverage": (pred_nonempty - hall) / max(1, pred_nonempty),
        "critical_unsupported_fill_rate": sum(
            metrics[k]["unsupported"] for k in CRITICAL) / max(1, sum(
                field[k]["pred_nonempty"] for k in CRITICAL)),
        "per_document": rows,
        "note": ("CER/WER, IoU/mAP, table cell association, latency and PSNR/SSIM "
                 "require corresponding OCR/box/table/timing/image predictions and "
                 "are not fabricated here."),
    }
    Path(a.output).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("field_metrics", "per_document")}, indent=2))


if __name__ == "__main__":
    main()
