#!/usr/bin/env python3
"""Categorize extraction errors per field and per document.

Categories: OCR_ERROR, LAYOUT_ERROR, TABLE_ERROR, FIELD_ASSOCIATION_ERROR,
SEMANTIC_CLASSIFICATION_ERROR, VALUE_EXTRACTION_ERROR, HALLUCINATION,
MISSING_FIELD, WRONG_FIELD, WRONG_ROW, JSON_ERROR, VALIDATION_ERROR,
SUPER_RESOLUTION_ERROR, SR_ORIGINAL_DISAGREEMENT.

Heuristic attribution from (gold, pred, evidence, warnings):
  - pred empty, gold non-empty, gold not in evidence  -> OCR_ERROR
  - pred empty, gold non-empty, gold in evidence      -> MISSING_FIELD
  - pred non-empty, gold empty                        -> HALLUCINATION
  - both non-empty, same number different field       -> FIELD_ASSOCIATION_ERROR
  - both non-empty, amount differs                    -> VALUE_EXTRACTION_ERROR
  - amount right but attached to another predicted field -> WRONG_FIELD
  - row-level table confusion (from warnings)         -> TABLE_ERROR / WRONG_ROW

Usage:
  .venv/bin/python scripts/error_analysis.py dataset/val.jsonl work/pred_val_orig.jsonl \\
      --out-dir reports
Writes reports/error_analysis.json and reports/error_analysis.csv.
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate import CURRENCY_FIELDS, FIELDS, NUMBER_RE, _norm_num, field_equal, norm  # noqa: E402


def _in_evidence(value: str, ev: str) -> bool:
    v = norm(value)
    if not v:
        return True
    m = NUMBER_RE.search(v)
    if m:
        nums = {_norm_num(x.group(0)) for x in NUMBER_RE.finditer(ev)}
        return _norm_num(m.group(0)) in nums
    return v in ev


def categorize(field, gold, pred, ev, warnings) -> str:
    g, p = norm(gold), norm(pred)
    if field_equal(field, gold, pred):
        return ""
    if not p and g:
        return "OCR_ERROR" if not _in_evidence(gold, ev) else "MISSING_FIELD"
    if p and not g:
        return "WRONG_FIELD" if _in_evidence(pred, ev) else "HALLUCINATION"
    if field in CURRENCY_FIELDS:
        gm, pm = NUMBER_RE.search(g), NUMBER_RE.search(p)
        if gm and pm and _norm_num(gm.group(0)) == _norm_num(pm.group(0)):
            return "FIELD_ASSOCIATION_ERROR"
        if pm and not _in_evidence(pred, ev):
            return "HALLUCINATION"
        return "VALUE_EXTRACTION_ERROR"
    if not _in_evidence(pred, ev):
        return "HALLUCINATION"
    if any("lexicon" in w for w in warnings):
        return "SEMANTIC_CLASSIFICATION_ERROR"
    return "VALUE_EXTRACTION_ERROR"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ground_truth")
    ap.add_argument("predictions")
    ap.add_argument("--out-dir", default="reports")
    a = ap.parse_args()
    gt = {json.loads(l)["id"]: json.loads(l) for l in open(a.ground_truth) if l.strip()}
    pr = {json.loads(l)["id"]: json.loads(l) for l in open(a.predictions) if l.strip()}
    rows, per_field, cats = [], {k: Counter() for k in FIELDS}, Counter()
    for i, g in gt.items():
        if i not in pr:
            continue
        gold, pred = g["ground_truth"], pr[i]["ground_truth"]
        ev, warns = norm(pr[i].get("evidence", "")), pr[i].get("warnings", [])
        doc_cats = Counter()
        for k in FIELDS:
            c = categorize(k, gold.get(k), pred.get(k), ev, warns)
            if c:
                rows.append({"id": i, "field": k, "category": c,
                             "gold": gold.get(k), "pred": pred.get(k)})
                per_field[k][c] += 1
                doc_cats[c] += 1
                cats[c] += 1
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = {"documents": len({r['id'] for r in rows}),
               "matched": len([i for i in gt if i in pr]),
               "errors": len(rows),
               "by_category": dict(cats),
               "by_field": {k: dict(v) for k, v in per_field.items() if v}}
    (out / "error_analysis.json").write_text(json.dumps(
        {"summary": summary, "errors": rows}, indent=2))
    with open(out / "error_analysis.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "field", "category", "gold", "pred"])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
