#!/usr/bin/env python3
"""Handwriting-leg eval: transcribe ANNOTATION crops, score exact match.

Usage:
  .venv/bin/python scripts/eval_handwriting.py --split val --count 15
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from invoice_extractor.handwriting import second_opinion  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--count", type=int, default=15)
    ap.add_argument("--root", default="dataset")
    a = ap.parse_args()
    root = Path(a.root)
    rows = [json.loads(x) for x in open(root / f"{a.split}.jsonl") if x.strip()]
    crops, refs, ids = [], [], []
    for rec in rows:
        if len(crops) >= a.count:
            break
        img = Image.open(root / rec["image"]).convert("RGB")
        for b in rec.get("regions", []):
            if b["field"] == "ANNOTATION" and len(crops) < a.count:
                x1, y1, x2, y2 = [int(v) for v in b["bbox"]]
                crops.append(img.crop((x1, y1, x2, y2)))
                refs.append(b["text"])
                ids.append(rec["id"])
    hyps = second_opinion(crops)
    exact = sum(h.strip().casefold() == r.casefold() for h, r in zip(hyps, refs))
    for i, h, r in zip(ids, hyps, refs):
        print(f"{i}: gold={r!r} pred={h!r} {'OK' if h.strip().casefold() == r.casefold() else 'MISS'}")
    print(f"exact: {exact}/{len(refs)} = {exact / max(1, len(refs)):.3f}")


if __name__ == "__main__":
    main()
