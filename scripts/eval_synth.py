#!/usr/bin/env python3
"""End-to-end evaluation on held-out InvoiceLab documents (digital PDFs and scanned PDFs).

For every document the full pipeline runs (pipeline.extract_invoice), then:
  * 31-field accuracy (format-aware, scoring.py), split by unseen vs seen label wording
  * line items: amount recall + category accuracy, split by unseen vs seen charge wording
  * citations: for every non-empty field, IoU between the cited box and the ground-truth union box
    of that value's words (DOM-measured; pushed through the scan homography for scans).
    citation_accuracy = share of cited values whose best IoU >= 0.5 (correct place on the page);
    uncited_rate = share of non-empty predicted values with no box at all.

  .venv/bin/python scripts/eval_synth.py dataset_v2 --split test --source scan --n 150 --out reports/synth_test_scan.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.scoring import aggregate, score_document, score_line_items  # noqa: E402

CODE_OF = {"Invoice No": ["invoice_number"], "Invoice Date": ["invoice_date"], "Container Number": ["container_number"],
           "MAWB No": ["mawb_number"], "HAWB No": ["hawb_number"], "Vendor Name": ["vendor_name"],
           "Bill of Lading / HBL": ["bill_of_lading_hbl", "master_bl"], "WEIGHT": ["weight", "chargeable_weight"],
           "Ex Rate": ["exchange_rate"]}
PT_PER_DEVICE_PX = 0.375


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def gt_boxes(ann: dict, codes: list[str], scale: float) -> list[tuple[int, list[float]]]:
    """Union box per contiguous run of value-tagged words, per page, in citation coordinates."""
    out = []
    for p, pg in enumerate(ann["pages"]):
        run = []
        for w in pg["words"] + [{"k": "", "b": [0, 0, 0, 0]}]:
            if w["k"] in [f"{c}:value" for c in codes] or w.get("k2") in [f"{c}:value" for c in codes]:
                run.append(w["b"])
                continue
            if run:
                out.append((p + 1, [min(b[0] for b in run) * scale, min(b[1] for b in run) * scale,
                                    max(b[2] for b in run) * scale, max(b[3] for b in run) * scale]))
                run = []
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--split", default="val")
    ap.add_argument("--source", choices=["digital", "scan"], default="digital")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--workers", type=int, default=1, help="documents processed concurrently (reader server batches)")
    a = ap.parse_args()
    from invoice_extractor.pipeline import extract_invoice

    root = Path(a.root)
    rows = {json.loads(l)["id"]: json.loads(l) for l in (root / f"{a.split}.jsonl").open()}
    if a.source == "digital":
        docs = [json.loads(l) for l in (root / "pdf_index.jsonl").open()]
        docs = [{"id": d["id"], "path": root / d["pdf"], "ann": root / rows[d["id"]]["ann"], "scale": PT_PER_DEVICE_PX}
                for d in docs if d["split"] == a.split]
    else:
        docs = [json.loads(l) for l in (root / f"scans_{a.split}.jsonl").open()]
        # scan PDFs are rasterized by the pipeline at OCR_DPI and mapped back to PDF points:
        # scan pixel -> PDF point = 72 / scan dpi
        docs = [{"id": d["id"], "path": root / d["pdf"], "ann": root / d["ann"], "scale": 72.0 / d["degradation"]["dpi"],
                 "kind": d["degradation"]["kind"]} for d in docs if d.get("pdf")]
    random.Random(a.seed).shuffle(docs)
    docs = docs[:a.n]
    scored, per_doc, cit = [], [], {"cited": 0, "correct": 0, "uncited": 0, "values": 0}
    unseen = {"label_unseen": [0, 0], "label_seen": [0, 0], "charge_unseen": [0, 0], "charge_seen": [0, 0]}
    t0 = time.time()

    def run_one(d):
        try:
            return d, extract_invoice(str(d["path"])), None
        except Exception as exc:
            return d, None, f"{type(exc).__name__}: {exc}"[:300]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        outputs = list(pool.map(run_one, docs))
    for k, (d, res, err) in enumerate(outputs):
        row = rows[d["id"]]
        ann = json.loads(Path(d["ann"]).read_text())
        if err:
            per_doc.append({"id": d["id"], "error": err})
            continue
        fields, audit = res
        gt = row["ground_truth"]
        fs = score_document(gt, fields)
        gold_items = [{"description": i["desc"], "amount_inr": i["taxable"], "category": i["category"],
                       "bucket": i["alias_bucket"]} for i in row["items"]]
        it = score_line_items(gold_items, audit.get("line_items", []))
        scored.append({"fields": fs, "items": it})
        # unseen-wording slices
        for f, codes in CODE_OF.items():
            lab = next((row["labels"].get(c) for c in codes if row["labels"].get(c)), None)
            if not gt.get(f) or not lab:
                continue
            key = "label_unseen" if lab["bucket"] in ("val", "test") else "label_seen"
            unseen[key][0] += fs[f]["strict"]
            unseen[key][1] += 1
        preds = audit.get("line_items", [])
        for g in gold_items:
            m = next((p for p in preds if abs(float(p.get("amount_inr", -1)) - g["amount_inr"]) < 0.01), None)
            key = "charge_unseen" if g["bucket"] in ("val", "test") else "charge_seen"
            unseen[key][1] += 1
            unseen[key][0] += bool(m and m.get("category") == g["category"])
        # citation accuracy for header fields
        for f, codes in CODE_OF.items():
            if not fields.get(f):
                continue
            cit["values"] += 1
            hits = (audit.get("citations") or {}).get(f) or []
            if not hits:
                cit["uncited"] += 1
                continue
            cit["cited"] += 1
            gtb = gt_boxes(ann, codes, d["scale"])
            best = max((iou(h["bbox"], b) for h in hits for p, b in gtb if p == h.get("page", 1)), default=0.0)
            cit["correct"] += best >= 0.5
        per_doc.append({"id": d["id"], "errors": {k2: [v["gold"], v["pred"]] for k2, v in fs.items() if not v["strict"]},
                        "route": audit.get("review_route"), "warnings": audit.get("warnings", [])[:6],
                        "kind": d.get("kind", "digital")})
        if (k + 1) % 10 == 0:
            print(f"{k + 1}/{len(docs)} docs  {time.time() - t0:.0f}s", flush=True)
    summary = aggregate(scored)
    summary["unseen_slices"] = {k: (round(v[0] / v[1], 4) if v[1] else None, v[1]) for k, v in unseen.items()}
    summary["citations"] = {"citation_accuracy_iou50": round(cit["correct"] / max(1, cit["cited"]), 4),
                            "uncited_rate": round(cit["uncited"] / max(1, cit["values"]), 4), **cit}
    summary["auto_accept_rate"] = round(sum(p.get("route") == "AUTO_ACCEPT" for p in per_doc) / max(1, len(per_doc)), 4)
    auto = [s for s, p in zip(scored, [p for p in per_doc if "error" not in p]) if p.get("route") == "AUTO_ACCEPT"]
    summary["auto_accept_precision_invoice"] = (round(sum(all(v["strict"] for v in s["fields"].values()) for s in auto) / len(auto), 4)
                                                if auto else None)
    summary["errors"] = sum("error" in p for p in per_doc)
    summary["seconds_per_doc"] = round((time.time() - t0) / max(1, len(docs)), 2)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"split": a.split, "source": a.source, "n": len(docs), "summary": summary,
                                       "documents": per_doc}, indent=2, ensure_ascii=False))
    s, l = summary["strict"], summary.get("line_items", {})
    print(json.dumps({"n": len(docs), "critical": round(s["critical_field_accuracy"], 4), "charges": round(s["charge_field_accuracy"], 4),
                      "invoice_exact": round(s["invoice_exact_match"], 4), "line_items": l, "unseen": summary["unseen_slices"],
                      "citations": summary["citations"], "auto_accept": summary["auto_accept_rate"],
                      "auto_accept_precision": summary["auto_accept_precision_invoice"]}, indent=1))


if __name__ == "__main__":
    main()
