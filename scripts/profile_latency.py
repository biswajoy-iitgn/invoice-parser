#!/usr/bin/env python3
"""Stage-level latency profiling: p50/p95 per stage + pages/sec.

Splits timing into document loading, quality assessment, (optional) SR,
ingest/OCR, LLM extraction, grounding/validation, serialization. Run on a
few clean and a few low-resolution documents and compare.

Usage:
  .venv/bin/python scripts/profile_latency.py --split val --count 5
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_extractor.extract import call_llm  # noqa: E402
from invoice_extractor.ingest import load_pages, pages_to_prompt_text, pages_to_raw_text  # noqa: E402
from invoice_extractor.quality import assess  # noqa: E402
from PIL import Image  # noqa: E402


def profile(image: str, model: str) -> dict:
    t = {}
    t0 = time.time()
    img = Image.open(image).convert("RGB")
    t["load"] = time.time() - t0
    t0 = time.time()
    q = assess(img)
    t["quality"] = time.time() - t0
    t["low_resolution"] = q.low_resolution
    t0 = time.time()
    pages = load_pages(image)
    t["ingest_ocr"] = time.time() - t0
    doc = pages_to_prompt_text(pages)
    t0 = time.time()
    call_llm(doc, model)
    t["llm"] = time.time() - t0
    t["total"] = sum(v for k, v in t.items() if k != "low_resolution")
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--model", default="qwen3-vl:8b-instruct")
    ap.add_argument("--output", default="work/profile.json")
    a = ap.parse_args()
    root = Path(a.root)
    rows = [json.loads(x) for x in open(root / f"{a.split}.jsonl") if x.strip()][:a.count]
    clean, low = [], []
    for rec in rows:
        for key, bucket in (("image", clean), ("lr_path", low)):
            img = (root / rec["image"]) if key == "image" else (root / rec["sr_pair"]["lr_path"])
            r = profile(str(img), a.model)
            r["id"] = rec["id"]
            bucket.append(r)
            print(f"{rec['id']} {key}: total={r['total']:.1f}s {r}", flush=True)
    summary = {}
    for name, bucket in (("clean", clean), ("low_resolution", low)):
        totals = [r["total"] for r in bucket]
        summary[name] = {
            "n": len(bucket),
            "p50_sec": statistics.median(totals),
            "p95_sec": max(totals),
            "pages_per_sec": len(bucket) / sum(totals) if sum(totals) else 0,
            "stages_p50": {k: statistics.median([r[k] for r in bucket])
                           for k in ("load", "quality", "ingest_ocr", "llm")},
        }
    Path(a.output).write_text(json.dumps({"summary": summary, "clean": clean, "low": low}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
