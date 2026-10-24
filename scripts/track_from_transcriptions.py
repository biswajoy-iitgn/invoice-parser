#!/usr/bin/env python3
"""Mac-side reader + metrics over GPU transcriptions.

Takes transcribed_*.jsonl (from scripts/paddle_transcribe.py on the L4),
runs each page set through the IDENTICAL Mac reader (ollama qwen3-vl:8b +
LAYA, via extract_from_pages), scores with track_pipelines metric code, and
writes work/track/pred_<pipe>_<cond>.jsonl rows consumable by
scripts/aggregate_track.py.

Usage (.venv):
  .venv/bin/python scripts/track_from_transcriptions.py \\
      --in transcribed_s3.jsonl --pipe D --work work/track
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

from track_pipelines import (  # noqa: E402
    CRITICAL,
    _strip_markup,
    cer_wer,
    field_metrics,
    reading_order,
    supported_field,
)
from invoice_extractor.extract import extract_from_pages  # noqa: E402
from invoice_extractor.ingest import Page  # noqa: E402
from invoice_extractor.schema import OUTPUT_FIELDS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--pipe", required=True)
    ap.add_argument("--work", default="work/track")
    a = ap.parse_args()

    os.environ["READER_BACKEND"] = "ollama"
    root = ROOT / "dataset"
    gt = {json.loads(l)["id"]: json.loads(l) for l in open(root / "val.jsonl")
          if l.strip()}
    rows = [json.loads(l) for l in open(a.inp) if l.strip()]
    print(f"scoring {len(rows)} transcriptions -> pipe {a.pipe}", flush=True)

    by_cond: dict[str, list] = {}
    for r in rows:
        by_cond.setdefault(r["cond"], []).append(r)
    for cond, items in sorted(by_cond.items()):
        out_path = Path(a.work) / f"pred_{a.pipe}_{cond}.jsonl"
        if out_path.exists():
            print(f"[{a.pipe}/{cond}] exists, skip", flush=True)
            continue
        with open(out_path, "w") as f:
            for i, r in enumerate(items):
                rec = gt[r["id"]]
                img = str(root / (rec["image"] if cond == "orig"
                                   else rec["sr_pair"]["lr_path"]))
                pages = [Page(p["number"], p["text"], p["raw_text"],
                              p["source"], p.get("notes", []))
                         for p in r["pages"]]
                t0 = time.time()
                try:
                    if r.get("error") or not pages:
                        raise RuntimeError(r.get("error") or "no pages transcribed")
                    fields, audit = extract_from_pages(
                        pages, img, t_ingest=r.get("ingest_s", 0.0))
                    err = ""
                except Exception as exc:
                    fields, audit = {}, {}
                    err = str(exc)[:200]
                dt = time.time() - t0
                gt_text = "\n".join(
                    b["text"] for b in sorted(rec["regions"],
                                              key=lambda b: (b["bbox"][1], b["bbox"][0])))
                ev = audit.get("evidence", "") if isinstance(audit, dict) else ""
                tr = audit.get("transcription", ev) if isinstance(audit, dict) else ""
                m = cer_wer(_strip_markup(gt_text), _strip_markup(tr))
                m_raw = cer_wer(gt_text, tr)
                ro = reading_order(rec["regions"], tr)
                fm = field_metrics(rec["ground_truth"], fields)
                inv_em = all(v["em"] for v in fm.values())
                crit_em = all(fm[k]["em"] for k in CRITICAL)
                unsup = sum(1 for k in OUTPUT_FIELDS
                            if str(fields.get(k) or "") and not supported_field(
                                k, str(fields.get(k)), ev))
                timing = audit.get("timing_sec", {}) if isinstance(audit, dict) else {}
                timing["ingest"] = r.get("ingest_s", 0.0)
                f.write(json.dumps({
                    "id": r["id"], "pipe": a.pipe, "cond": cond,
                    "vendor": rec.get("vendor_id"), "error": err,
                    "fields": fields, "field_metrics": fm,
                    "invoice_em": inv_em, "critical_em": crit_em,
                    "cer": m["cer"], "wer": m["wer"],
                    "raw_cer": m_raw["cer"], "coverage": m["coverage"],
                    "tok_hall": m["tok_hall"], "ned": m["ned"],
                    "read_order": ro, "unsupported": unsup,
                    "nonempty": sum(1 for v in fields.values() if str(v or "")),
                    "timing": timing, "total_s": round(dt + r.get("ingest_s", 0.0), 2),
                    "sr_s": 0.0,
                    "citation_coverage": audit.get("citation_coverage", 0)
                    if isinstance(audit, dict) else 0,
                }) + "\n")
                f.flush()
                print(f"[{a.pipe}/{cond}] {i+1}/{len(items)} {r['id']} em={inv_em} "
                      f"cer={m['cer']:.3f} dt={dt:.1f}s {err[:60]}", flush=True)


if __name__ == "__main__":
    main()
