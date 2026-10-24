#!/usr/bin/env python3
"""Paddle track: PP-StructureV3 (+ optional PaddleOCR-VL) on the same 50 docs.

Runs under ~/paddle-env (paddlepaddle CPU per Paddle's Apple Silicon guide):
  ~/paddle-env/bin/python scripts/track_paddle.py --cond orig,lr

Same doc IDs as work/track/pred_A_orig.jsonl, same metric code as
scripts/track_pipelines.py (cer_wer, reading_order, field_metrics).
Outputs work/track/pred_<pipe>_<cond>.jsonl for scripts/aggregate_track.py.

PaddleOCR-VL markdown is used when INVOICE_VL=on (same flag as ingest);
default is PP-StructureV3 only.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("PADDLE_DEVICE", "cpu")

from track_pipelines import (  # noqa: E402
    CRITICAL,
    _strip_markup,
    cer_wer,
    field_metrics,
    reading_order,
    supported_field,
)
from invoice_extractor.extract import extract_invoice  # noqa: E402
from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipe", default="D")
    ap.add_argument("--conds", default="orig,lr")
    ap.add_argument("--work", default="work/track")
    ap.add_argument("--ids-from", default="work/track/pred_A_orig.jsonl")
    a = ap.parse_args()

    os.environ["INVOICE_INGEST"] = "paddle"
    os.environ["READER_BACKEND"] = "ollama"
    vl = os.environ.get("INVOICE_VL", "off").lower() in {"1", "on", "true", "yes"}
    print(f"paddle track pipe={a.pipe} vl={'on' if vl else 'off'}", flush=True)

    work = Path(a.work)
    root = ROOT / "dataset"
    ids = [json.loads(l)["id"] for l in open(work / Path(a.ids_from).name) if l.strip()]
    by_id = {json.loads(l)["id"]: json.loads(l) for l in open(root / "val.jsonl") if l.strip()}
    docs = [by_id[i] for i in ids]
    print(f"tracking {len(docs)} docs", flush=True)

    for cond in a.conds.split(","):
        out_path = work / f"pred_{a.pipe}_{cond}.jsonl"
        if out_path.exists():
            have = sum(1 for _ in open(out_path))
            if have >= len(docs):
                print(f"[{a.pipe}/{cond}] exists ({have}), skip", flush=True)
                continue
            print(f"[{a.pipe}/{cond}] partial ({have}/{len(docs)}), redo", flush=True)
        with open(out_path, "w") as f:
            for i, rec in enumerate(docs):
                img = str(root / (rec["image"] if cond == "orig"
                                   else rec["sr_pair"]["lr_path"]))
                t0 = time.time()
                try:
                    fields, audit = extract_invoice(img)
                    err = ""
                except Exception as exc:
                    fields, audit = {}, {"error": str(exc)[:200], "timing_sec": {}}
                    err = str(exc)[:200]
                dt = time.time() - t0
                gt_text = "\n".join(
                    b["text"] for b in sorted(rec["regions"],
                                              key=lambda b: (b["bbox"][1], b["bbox"][0])))
                ev = audit.get("evidence", "")
                tr = audit.get("transcription", ev)
                m = cer_wer(_strip_markup(gt_text), _strip_markup(tr))
                m_raw = cer_wer(gt_text, tr)
                ro = reading_order(rec["regions"], tr)
                fm = field_metrics(rec["ground_truth"], fields)
                inv_em = all(v["em"] for v in fm.values())
                crit_em = all(fm[k]["em"] for k in CRITICAL)
                unsup = sum(1 for k in OUTPUT_FIELDS
                            if str(fields.get(k) or "") and not supported_field(
                                k, str(fields.get(k)), ev))
                f.write(json.dumps({
                    "id": rec["id"], "pipe": a.pipe, "cond": cond,
                    "vendor": rec.get("vendor_id"), "error": err,
                    "fields": fields, "field_metrics": fm,
                    "invoice_em": inv_em, "critical_em": crit_em,
                    "cer": m["cer"], "wer": m["wer"],
                    "raw_cer": m_raw["cer"], "coverage": m["coverage"],
                    "tok_hall": m["tok_hall"], "ned": m["ned"],
                    "read_order": ro, "unsupported": unsup,
                    "nonempty": sum(1 for v in fields.values() if str(v or "")),
                    "timing": audit.get("timing_sec", {}), "total_s": round(dt, 2),
                    "sr_s": 0.0,
                    "citation_coverage": audit.get("citation_coverage", 0),
                }) + "\n")
                f.flush()
                print(f"[{a.pipe}/{cond}] {i+1}/{len(docs)} {rec['id']} em={inv_em} "
                      f"cer={m['cer']:.3f} dt={dt:.1f}s {err[:60]}", flush=True)


if __name__ == "__main__":
    main()
